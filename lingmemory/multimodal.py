# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# 未经授权，不得外传、复制、逆向工程。
# ═══════════════════════════════════════════════

"""
灵码 多模态中间件 — 灵元V1.0插片

每种模态是一个插片，query灵忆拿到rule后调对应模型。
流转不变（理解→检索→生成→验证→输出），变化的只是出入格式。

模态插片：
  文本→文本：DeepSeek/GLM（已有）
  图片→文本：识图/读图/看图解题
  文本→图片：文生图/架构图/流程图
  截图→诊断：UI截图分析/报错截图解读
  文本→音频：TTS代码讲解
  音频→文本：ASR语音指令
  文本→视频：代码演示视频
"""
import json
import os
import base64
import urllib.request
from pathlib import Path
from typing import Optional

from lingmemory.api import LingMemoryAPI


class MultimodalGateway:
    """灵码多模态网关 — 所有模态调用的统一入口
    
    出入：各种格式的input → 各种格式的output
    流转：query rule → 调模型 → 验证 → 返回
    """

    def __init__(self, proxy_url=None,
                 caller="lingclaude", db_path=None):
        self.proxy_url = proxy_url or os.environ.get(
            "LINGCLAUDE_PROXY_URL", "http://127.0.0.1:8765/v1")
        self.caller = caller
        headers = {"X-Caller": caller, "X-Agent-Id": caller}
        self.api = LingMemoryAPI(db_path, member=caller) if db_path else None

    def _proxy_chat(self, model, messages, max_tokens=2000, images=None):
        """通过proxy调LLM（统一入口）"""
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        
        # 多模态：图片放message的image_url字段
        if images:
            content = []
            for img in images:
                if isinstance(img, str) and os.path.exists(img):
                    b64 = base64.b64encode(Path(img).read_bytes()).decode()
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
                elif isinstance(img, str):
                    content.append({"type": "image_url", "image_url": {"url": img}})
            content.append({"type": "text", "text": messages[-1]["content"]})
            messages[-1] = {"role": messages[-1]["role"], "content": content}

        req = urllib.request.Request(
            f"{self.proxy_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Caller": self.caller, "X-Agent-Id": self.caller},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            return json.loads(resp.read())["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[error: {e}]"

    def _query_rules(self, category=None, limit=5):
        """query灵忆拿适用rule"""
        if not self.api:
            return ""
        filters = {"type": "coding_rule"}
        result = self.api.lm.query(
            type="coding_rule", state="validated", limit=limit)
        rules = [item["data"].get("rule", "") for item in result["items"]]
        return "\n".join(f"- {r}" for r in rules[:limit])

    # ============================================================
    # 图片→文本：识图/读图/看图解题
    # ============================================================
    def understand_image(self, image_path: str, question: str = "描述这张图片") -> str:
        """识图：理解图片内容"""
        rules = self._query_rules()
        prompt = f"{question}\n\n参考编码规则:\n{rules}" if rules else question
        return self._proxy_chat(
            "qwen3-vl-flash@bailian",
            [{"role": "user", "content": prompt}],
            images=[image_path])

    def read_screenshot(self, screenshot_path: str, context: str = "") -> str:
        """读截图：分析UI/报错/日志截图"""
        prompt = f"分析这张截图"
        if context:
            prompt += f"，上下文：{context}"
        prompt += "。如果是报错信息，给出修复建议。如果是UI，描述布局和问题。"
        return self._proxy_chat(
            "qwen3-vl-flash@bailian",
            [{"role": "user", "content": prompt}],
            images=[screenshot_path])

    def solve_from_image(self, image_path: str, problem: str = "") -> str:
        """看图解题：从图表/代码截图/架构图中解答问题"""
        rules = self._query_rules(category="architecture")
        prompt = f"看图解答以下问题：{problem or '分析这张图并给出结论'}\n"
        if rules:
            prompt += f"\n参考架构规则:\n{rules}"
        return self._proxy_chat(
            "qwen3-vl-flash@bailian",
            [{"role": "user", "content": prompt}],
            images=[image_path], max_tokens=4000)

    # ============================================================
    # 文本→图片：文生图/架构图/流程图
    # ============================================================
    def generate_image(self, prompt: str, model: str = "z-image-turbo") -> str:
        """文生图：根据描述生成图片"""
        req = urllib.request.Request(
            f"{self.proxy_url}/images/generations",
            data=json.dumps({"model": model, "prompt": prompt, "n": 1}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Caller": self.caller, "X-Agent-Id": self.caller},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            return result.get("data", [{}])[0].get("url", "[no url]")
        except Exception as e:
            return f"[error: {e}]"

    def generate_architecture_diagram(self, description: str) -> str:
        """生成架构图"""
        prompt = f"技术架构图，简洁清晰，白色背景，中英文标注：{description}"
        return self.generate_image(prompt)

    def generate_flowchart(self, steps: list[str]) -> str:
        """生成流程图"""
        flow = " → ".join(steps)
        prompt = f"流程图，从左到右，简洁：{flow}"
        return self.generate_image(prompt)

    # ============================================================
    # 文本→音频：TTS代码讲解
    # ============================================================
    def speak_code(self, code: str, language: str = "python") -> str:
        """用语音讲解代码"""
        explanation = self._proxy_chat("deepseek-v4-flash", [
            {"role": "user", "content": f"用口语化中文解释这段{language}代码(100字内):\n{code[:500]}"}
        ], max_tokens=200)
        
        # 调灵声TTS
        tts_url = os.environ.get(
            "LINGVOICE_TTS_URL", "http://127.0.0.1:8100/synthesize")
        req = urllib.request.Request(
            tts_url,
            data=json.dumps({"text": explanation, "voice": "zh-CN-XiaoxiaoNeural"}).encode(),
            headers={"Content-Type": "application/json",
                     "X-API-Key": os.environ.get("LINGVOICE_API_KEY", "")},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read()).get("audio_url", explanation)
        except Exception as e:
            return explanation  # 降级：返回文本

    # ============================================================
    # 音频→文本：ASR语音指令
    # ============================================================
    def transcribe(self, audio_path: str) -> str:
        """语音转文字"""
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        b64 = base64.b64encode(audio_data).decode()
        
        req = urllib.request.Request(
            f"{self.proxy_url}/audio/transcriptions",
            data=json.dumps({
                "model": "qwen3-asr-flash@bailian",
                "audio": b64,
            }).encode(),
            headers={"Content-Type": "application/json",
                     "X-Caller": self.caller, "X-Agent-Id": self.caller},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            return json.loads(resp.read()).get("text", "[no text]")
        except Exception as e:
            return f"[error: {e}]"

    # ============================================================
    # 多模态组合：截图→诊断→修复→验证
    # ============================================================
    def diagnose_and_fix(self, screenshot_path: str) -> dict:
        """截图诊断全流程：读截图→分析→给修复方案"""
        # Step 1: 读截图
        analysis = self.read_screenshot(screenshot_path)
        
        # Step 2: query规则
        rules = self._query_rules(category="debugging")
        
        # Step 3: 生成修复方案
        fix = self._proxy_chat("deepseek-v4-flash", [
            {"role": "system", "content": f"你是代码修复专家。参考规则:\n{rules}"},
            {"role": "user", "content": f"截图分析结果:\n{analysis}\n\n给出修复方案（代码+解释）。"}
        ], max_tokens=2000)
        
        return {
            "analysis": analysis,
            "rules_applied": rules,
            "fix": fix,
        }

    # ============================================================
    # 文本→视频：代码演示（简化版）
    # ============================================================
    def generate_demo_video(self, code: str, description: str = "") -> str:
        """生成代码演示视频描述（实际视频生成走百炼异步）"""
        prompt = f"代码演示视频：{description or '展示代码运行效果'}\n代码：{code[:200]}"
        # 文生视频走proxy的videos端点
        req = urllib.request.Request(
            f"{self.proxy_url}/videos/generations",
            data=json.dumps({"model": "wan2.1-14b-t2v@bailian", "prompt": prompt}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Caller": self.caller, "X-Agent-Id": self.caller},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            return json.loads(resp.read()).get("data", [{}])[0].get("url", "[pending]")
        except Exception as e:
            return f"[error: {e}]"

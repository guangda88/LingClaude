# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════

"""
灵码 远程大模型论文/文档reader

读开源大模型的技术报告/README/design doc，
提取架构决策、训练方法、推理优化等高价值rule。
"""
import json
import re
import sys
import time
import urllib.request

sys.path.insert(0, "/home/ai/lingclaude")
from lingmemory.api import LingMemoryAPI


# 大模型仓库的README/技术文档
TARGETS = {
    "DeepSeek-V3": {
        "url": "https://raw.githubusercontent.com/deepseek-ai/DeepSeek-V3/main/README.md",
    },
    "DeepSeek-R1": {
        "url": "https://raw.githubusercontent.com/deepseek-ai/DeepSeek-R1/main/README.md",
    },
    "Qwen3": {
        "url": "https://raw.githubusercontent.com/QwenLM/Qwen3/main/README.md",
    },
    "Qwen2.5-Coder": {
        "url": "https://raw.githubusercontent.com/QwenLM/Qwen2.5-Coder/main/README.md",
    },
    "LLaMA": {
        "url": "https://raw.githubusercontent.com/meta-llama/llama3/main/README.md",
    },
    "Mistral": {
        "url": "https://raw.githubusercontent.com/mistralai/mistral-src/main/README.md",
    },
    "StarCoder2": {
        "url": "https://raw.githubusercontent.com/bigcode-project/starcoder2/main/README.md",
    },
    "MoE-Survey": {
        "url": "https://raw.githubusercontent.com/taichengguo/LLM_MoE_Survey/master/README.md",
    },
    "vLLM": {
        "url": "https://raw.githubusercontent.com/vllm-project/vllm/main/README.md",
    },
    "Ollama": {
        "url": "https://raw.githubusercontent.com/ollama/ollama/main/README.md",
    },
    "llama.cpp": {
        "url": "https://raw.githubusercontent.com/ggerganov/llama.cpp/master/README.md",
    },
    "MLC-LLM": {
        "url": "https://raw.githubusercontent.com/mlc-ai/mlc-llm/main/README.md",
    },
    "transformers": {
        "url": "https://raw.githubusercontent.com/huggingface/transformers/main/README.md",
    },
    "OpenWebUI": {
        "url": "https://raw.githubusercontent.com/open-webui/open-webui/main/README.md",
    },
    "SGLang": {
        "url": "https://raw.githubusercontent.com/sgl-project/sglang/main/README.md",
    },
    "Awesome-LLM": {
        "url": "https://raw.githubusercontent.com/Hannibal046/Awesome-LLM/main/README.md",
    },
    "LiteLLM": {
        "url": "https://raw.githubusercontent.com/BerriAI/litellm/main/README.md",
    },
    "ARC-AGI": {
        "url": "https://raw.githubusercontent.com/arcprize/ARC-AGI/master/README.md",
    },
    "OpenAI-CoT": {
        "url": "https://raw.githubusercontent.com/openai/openai-cookbook/main/examples/Techniques_to_improve_reliability.md",
    },
}

# 从文档中提取的rule模式
DOC_PATTERNS = [
    # 架构决策
    (r"MoE|mixture.of.experts|sparse.expert",
     "MoE架构: 稀疏激活降低推理成本 只激活部分专家", "architecture"),
    (r"Multi.?Head.?Latent.?Attention|MLA",
     "MLA(Multi-Head Latent Attention): 压缩KV缓存降低内存", "architecture"),
    (r"DualPipe|pipeline.parallelism|tensor.parallel",
     "流水线并行+张量并行: 多GPU训练的并行策略", "architecture"),
    (r"FP8|INT4|INT8|quantiz|GPTQ|AWQ|GGUF",
     "量化(FP8/INT4/AWQ/GGUF): 降低模型体积和推理成本", "architecture"),
    (r"Grouped.?Query.?Attention|GQA",
     "GQA(Grouped Query Attention): 共享KV头降低显存", "architecture"),
    (r"Sliding.?Window.?Attention|SWA",
     "滑动窗口注意力: 长上下文的局部注意力降低计算量", "architecture"),
    (r"RoPE|rotary.position|rotary.embedding",
     "RoPE旋转位置编码: 支持长上下文外推", "architecture"),
    (r"Flash.?Attention|flash_attn",
     "Flash Attention: 减少IO的注意力计算加速", "architecture"),
    
    # 训练方法
    (r"RLHF|reinforcement.learning|PPO|DPO",
     "RLHF/DPO: 人类反馈对齐模型输出偏好", "pattern"),
    (r"GRPO|group.relative.policy",
     "GRPO: 无需value模型的强化学习(DeepSeek)", "pattern"),
    (r"distill|knowledge.distill",
     "知识蒸馏: 大模型能力迁移到小模型", "pattern"),
    (r"SFT|supervised.fine.?tun",
     "SFT(监督微调): 在高质量数据上微调基座模型", "pattern"),
    (r"chain.of.thought|CoT|reasoning.chain",
     "Chain-of-Thought: 让模型展示推理过程提升准确率", "pattern"),
    (r"test.?time.?comput|reasoning.?model|thinking.token",
     "Test-time compute: 推理时增加计算量提升质量(如o1/R1)", "pattern"),
    (r"LoRA|QLoRA|low.rank.adapt",
     "LoRA/QLoRA: 低秩适配器微调(参数高效GPU友好)", "pattern"),
    (r"prefix.?cach|KV.?cache.?reuse",
     "Prefix cache: 复用KV缓存加速多轮对话", "pattern"),
    
    # 推理优化
    (r"PagedAttention|paged.attention",
     "PagedAttention: 分页管理KV缓存(vLLM)减少碎片", "pattern"),
    (r"continuous.batching|iteration.level.batching",
     "Continuous batching: 动态拼批提升吞吐量(vLLM/SGLang)", "pattern"),
    (r"speculative.decoding|speculat",
     "Speculative decoding: 小模型草稿+大模型验证加速推理", "pattern"),
    (r"tensor.parallel|TP\)",
     "张量并行(TP): 单层拆到多GPU(通信密集)", "pattern"),
    (r"GGUF|llama\.cpp",
     "GGUF格式: CPU/边缘设备运行量化模型(llama.cpp)", "pattern"),
    (r"NPU|edge.deploy|on.device|mobile.deploy",
     "端侧部署: NPU/手机运行量化后的小模型", "pattern"),
    (r"vocab|tokenizer|BPE|SentencePiece",
     "Tokenizer选择: BPE/SentencePiece影响多语言能力和效率", "pattern"),
    (r"context.length|long.context|128k|200k|1M",
     "长上下文(128K-1M): RoPE外推+滑动窗口+KV缓存压缩", "architecture"),
    
    # 工程实践
    (r"OpenAI.compat|/v1/chat/completions",
     "OpenAI兼容API(/v1/chat/completions): 降低接入成本", "pattern"),
    (r"function.call|tool.use|tool.calling",
     "Function call: 让LLM调用外部工具(搜索/代码/API)", "pattern"),
    (r"RAG|retrieval.augmented|retrieval.augment",
     "RAG: 检索增强生成 外挂知识库不微调", "pattern"),
    (r"agent|tool.use|autonomous",
     "Agent: LLM+工具调用+记忆=自主任务执行", "pattern"),
    (r"guardrail|safety.filter|content.filter",
     "Guardrail: 输入输出安全过滤(防注入/有害内容)", "security"),
    (r"embed|embedding.model",
     "Embedding模型: 文本向量化用于检索/聚类/分类", "pattern"),
    (r"benchmark|HumanEval|MMLU|GSM8K|eval",
     "Benchmark评测: HumanEval(代码)MMLU(知识)GSM8K(数学)", "pattern"),
    (r"multi.?modal|vision|image.input|VLM",
     "多模态: 文本+图片+音频统一模型(VLM)", "pattern"),
    (r"watermark|provenance",
     "水印: 检测AI生成内容的技术(provenance)", "security"),
    (r"red.?team|adversarial",
     "红队测试: 主动攻击自己的模型发现漏洞", "security"),
]


def fetch(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "lingshell-proxy/1.0"})
        resp = urllib.request.urlopen(req, timeout=20)
        return resp.read().decode("utf-8", errors="replace")
    except:
        return ""


def run():
    api = LingMemoryAPI(member="lingclaude")
    stats = {"docs": 0, "rules": 0, "new": 0, "errors": 0}
    
    for name, config in TARGETS.items():
        url = config["url"]
        content = fetch(url)
        
        if not content or len(content) < 200:
            stats["errors"] += 1
            print(f"  ❌ {name}: 获取失败")
            continue
        
        stats["docs"] += 1
        found = []
        
        for pattern, rule_text, category in DOC_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                full = f"{name}: {rule_text}"
                found.append((full, category))
                stats["rules"] += 1
                
                existing = api.lm.conn.execute(
                    "SELECT COUNT(*) FROM records WHERE type='coding_rule' "
                    "AND json_extract(data,'$.rule')=?", (full,)).fetchone()[0]
                
                if existing == 0:
                    api.lm.create(type='coding_rule', data={
                        'rule': full, 'evidence': [f'llm_doc_{name}'],
                        'category': category, 'confidence': 0.75,
                        'source': 'llm_doc_reader',
                    }, created_by='lingclaude')
                    stats["new"] += 1
        
        print(f"  ✅ {name}: {len(content)}字符, 匹配{len(found)}模式, 新{sum(1 for f,c in found if not api.lm.conn.execute('SELECT COUNT(*) FROM records WHERE type=\"coding_rule\" AND json_extract(data,\"$.rule\")=?',(f,)).fetchone()[0])}条")
    
    total = api.lm.conn.execute("SELECT COUNT(*) FROM records WHERE type LIKE '%rule%'").fetchone()[0]
    coding = api.lm.conn.execute("SELECT COUNT(*) FROM records WHERE type='coding_rule'").fetchone()[0]
    
    print(f"\n=== 大模型文档reader结果 ===")
    print(f"文档: {stats['docs']}, 匹配: {stats['rules']}, 新rule: {stats['new']}, 错误: {stats['errors']}")
    print(f"coding_rule: {coding}条, 全领域rule: {total}条")
    
    api.close()


if __name__ == "__main__":
    run()

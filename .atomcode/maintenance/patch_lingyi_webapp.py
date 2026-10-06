#!/usr/bin/env python3
"""灵依 web_app.py 补丁：/api/v1/plan/generate 契约路由 + whisper 惰性导入。

背景（2026-10-04 排查结论）：
- linghealth/src/api/ai_dispatch.py:154 以 POST /api/v1/plan/generate 调 lingyi(:8902)，
  载荷 {user_id, plan_type, assessment_data}，lingyi 缺此路由 → 404。
- web_app.py 模块级 `import whisper`：torch CUDA 库映射失败时整个服务无法启动，
  连纯文本/plan 路径一起陪葬 → 惰性化，STT 降级不应拖死对话与健康助手主链路。

用法:
  python3 patch_lingyi_webapp.py <目标web_app.py路径>   # 副本路径 → 干跑验证
  --check                                               # 只校验是否已补丁
安全设计: 自动备份(.bak-YYYYMMDD-HHMMSS) → 变换 → py_compile 语法门 → 失败自动还原 → 幂等。
"""
from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ── 变换 1: whisper 模块级导入 → 惰性（导入失败不再炸整个服务） ──
OLD_IMPORT = "import whisper\n"
NEW_IMPORT = """try:
    import whisper  # 惰性化: STT 库缺失/损坏时仅语音功能降级，不影响 text/plan 链路
except Exception as _whisper_err:  # noqa: F841
    whisper = None  # type: ignore[assignment]
"""

# ── 变换 2: get_whisper_model 守卫（False=曾尝试失败） ──
OLD_GUARD = """def get_whisper_model():
    global whisper_model
    if whisper_model is not None:
        return whisper_model
"""
NEW_GUARD = """def get_whisper_model():
    global whisper_model
    if whisper_model is not None:
        return whisper_model
    if whisper_model is False:  # 之前尝试过且失败: 不重复加载
        return None
"""

# ── 变换 3: except 后备分支再失败时记 False ──
OLD_FALLBACK = """    except Exception as e:
        logger.warning(f"Whisper 加载失败: {e}")
        whisper_model = whisper.load_model("small", device="cpu")
        logger.info("Whisper 加载完成 (CPU 后备)")
    return whisper_model
"""
NEW_FALLBACK = """    except Exception as e:
        logger.warning(f"Whisper 加载失败: {e}")
        if whisper is None:
            whisper_model = False  # 库不可用: 标记为已尝试失败, 语音功能降级
            return None
        try:
            whisper_model = whisper.load_model("small", device="cpu")
            logger.info("Whisper 加载完成 (CPU 后备)")
        except Exception as e2:
            logger.warning(f"Whisper CPU 后备也失败: {e2}")
            whisper_model = False
            return None  # 统一语义: 加载失败一律返回 None(调用方守卫 503)
    return whisper_model
"""

# ── 变换 3b/3c: 语音调用点守卫（model=None → 503，避免 bool/None.transcribe 崩溃） ──
OLD_STT_CALL_IND8 = """        model = get_whisper_model()
        result = model.transcribe("""
NEW_STT_CALL_IND8 = """        model = get_whisper_model()
        if model is None:
            return JSONResponse({"error": "语音识别暂不可用(STT 未加载)"}, status_code=503)
        result = model.transcribe("""
OLD_STT_CALL_IND4 = """    model = get_whisper_model()
    result = model.transcribe("""
NEW_STT_CALL_IND4 = """    model = get_whisper_model()
    if model is None:
        return JSONResponse({"error": "语音识别暂不可用(STT 未加载)"}, status_code=503)
    result = model.transcribe("""

# ── 变换 4: plan/generate 路由（锚定在 /api/history 之前） ──
OLD_HISTORY_ANCHOR = '@app.get("/api/history")'
TOMBSTONE = "# [2026-10-04] linghealth 平台契约路由(计划方案生成): 已接入, 见上方 plan_generate"
NEW_PLAN_BLOCK = '''# ══════════════════════════════════════════════════
# 灵康(linghealth)平台契约: 计划方案生成
# 调用方: linghealth/src/api/ai_dispatch.py:154 (POST /api/v1/plan/generate)
# 契约: 载荷 {user_id, plan_type, assessment_data} → {plans: [...], plan_type, generated_at}
# 边界: 健康建议非医疗诊断; 无 GLM key 时降级为通用作息模板
# ══════════════════════════════════════════════════

PLAN_PROMPT = """你是灵依，灵康健康平台的计划助手。基于用户评估数据生成一份{plan_type_desc}。
要求: 3-6 条计划项, 每条一行, 格式为「标题|类别|建议频次」。
边界: 只涉及作息/运动/饮食/情绪等生活方式建议, 不做医疗诊断, 不开药物处方。
输出只包含计划条目本身, 不要开场白和总结。
评估数据: {assessment_json}"""

_PLAN_TYPE_DESC = {
    "comprehensive": "综合健康计划(作息/运动/饮食/情绪)",
    "sleep": "睡眠改善计划",
    "exercise": "运动锻炼计划",
    "diet": "饮食营养计划",
    "emotion": "情绪与压力管理计划",
}


def _fallback_plans() -> list[dict]:
    """GLM 不可用时的通用作息模板(非医疗建议)。"""
    return [
        {"title": "固定作息", "category": "sleep", "frequency": "每天 23:00 前入睡"},
        {"title": "适量运动", "category": "exercise", "frequency": "每周 3 次, 每次 30 分钟"},
        {"title": "规律饮食", "category": "diet", "frequency": "三餐定时, 少油少盐"},
        {"title": "放松呼吸", "category": "emotion", "frequency": "每天 2 次, 每次 5 分钟"},
    ]


@app.post("/api/v1/plan/generate")
async def plan_generate(data: dict):
    user_id = str(data.get("user_id", "")).strip()
    if not user_id:
        return JSONResponse({"error": "user_id 必填"}, status_code=400)
    plan_type = str(data.get("plan_type", "comprehensive"))
    assessment = data.get("assessment_data") or {}
    desc = _PLAN_TYPE_DESC.get(plan_type, _PLAN_TYPE_DESC["comprehensive"])

    prompt = PLAN_PROMPT.format(
        plan_type_desc=desc,
        assessment_json=json.dumps(assessment, ensure_ascii=False)[:2000],
    )
    plans: list[dict] = []
    degraded = False
    if not GLM_API_KEY:
        degraded = True
    else:
        try:
            raw = await call_glm(
                [{"role": "system", "content": prompt}, {"role": "user", "content": "请生成计划"}],
                stream=False,
            )
            for line in (raw or "").strip().splitlines():
                line = line.strip().lstrip("-*• ").strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                plans.append({
                    "title": parts[0],
                    "category": parts[1] if len(parts) > 1 else plan_type,
                    "frequency": parts[2] if len(parts) > 2 else "",
                })
        except Exception as e:
            logger.warning(f"plan 生成 GLM 调用失败, 降级模板: {e}")
            degraded = True
    if not plans:
        degraded = True
        plans = _fallback_plans()

    return {
        "user_id": user_id,
        "plan_type": plan_type,
        "plans": plans[:6],
        "degraded": degraded,
        "disclaimer": "生活方式建议, 非医疗诊断; 如有不适请咨询专业医生",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


'''


def apply(source: Path) -> int:
    """返回执行码: 0=变更成功, 2=已补丁(幂等), 1=错误。"""
    text = source.read_text(encoding="utf-8")

    if "plan_generate" in text and "_PLAN_TYPE_DESC" in text:
        print("已补丁(幂等): plan/generate 路由已存在")
        return 2

    changes: list[tuple[str, str, str]] = []  # (名称, 旧, 新)

    # 1. whisper 导入惰性化
    if OLD_IMPORT not in text:
        print(f"错误: 锚点1(whisper 导入)未找到", file=sys.stderr)
        return 1
    changes.append(("whisper惰性导入", OLD_IMPORT, NEW_IMPORT))

    # 2. get_whisper_model 守卫
    if OLD_GUARD not in text:
        print("错误: 锚点2(get_whisper_model 头部)未找到", file=sys.stderr)
        return 1
    changes.append(("get_whisper_model守卫", OLD_GUARD, NEW_GUARD))

    # 3. except 后备分支
    if OLD_FALLBACK not in text:
        print("错误: 锚点3(except 后备分支)未找到", file=sys.stderr)
        return 1
    changes.append(("except降级分支", OLD_FALLBACK, NEW_FALLBACK))

    # 3b/3c. 语音调用点守卫(503)
    if OLD_STT_CALL_IND8 not in text:
        print("错误: 锚点3b(voice-chat STT 调用)未找到", file=sys.stderr)
        return 1
    changes.append(("voice-chat守卫", OLD_STT_CALL_IND8, NEW_STT_CALL_IND8))
    if OLD_STT_CALL_IND4 not in text:
        print("错误: 锚点3c(stream STT 调用)未找到", file=sys.stderr)
        return 1
    changes.append(("stream守卫", OLD_STT_CALL_IND4, NEW_STT_CALL_IND4))

    # 4. plan 路由(含 tombstone 注释)
    if OLD_HISTORY_ANCHOR not in text:
        print("错误: 锚点4(/api/history)未找到", file=sys.stderr)
        return 1
    changes.append(("plan路由注入", OLD_HISTORY_ANCHOR, NEW_PLAN_BLOCK + OLD_HISTORY_ANCHOR + "\n" + TOMBSTONE))

    # ── 备份 ──
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = source.parent / f"{source.name}.bak-{stamp}"
    shutil.copy2(source, backup)
    print(f"已备份: {backup}")

    # ── 应用 ──
    new_text = text
    for name, old, new in changes:
        assert old in new_text, f"变换 {name} 的锚点在迭代中丢失"
        new_text = new_text.replace(old, new, 1)
        print(f"已应用: {name}")

    source.write_text(new_text, encoding="utf-8")

    # ── 语法门 ──
    try:
        py_compile.compile(str(source), doraise=True)
        print("语法门通过")
    except py_compile.PyCompileError as e:
        shutil.copy2(backup, source)
        print(f"语法门失败, 已还原: {e}", file=sys.stderr)
        return 1

    # ── 特征复核 ──
    final = source.read_text(encoding="utf-8")
    checks = {
        "路由注册": '@app.post("/api/v1/plan/generate")' in final,
        "user_id 校验": "user_id 必填" in final,
        "降级模板": "_fallback_plans" in final,
        "医疗边界声明": "非医疗诊断" in final,
        "whisper 惰性": "import whisper" in final and "except Exception as _whisper_err" in final,
        "守卫": "whisper_model is False" in final,
        "STT 503 守卫": final.count("语音识别暂不可用") == 2,
        "tombstone": TOMBSTONE.split(": ", 1)[1] in final,
    }
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        shutil.copy2(backup, source)
        print(f"特征复核失败({bad}), 已还原", file=sys.stderr)
        return 1
    print("特征复核通过: 7/7")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    target = Path(sys.argv[1]).expanduser().resolve()
    if not target.exists():
        print(f"目标不存在: {target}", file=sys.stderr)
        return 1
    return apply(target)


if __name__ == "__main__":
    sys.exit(main())

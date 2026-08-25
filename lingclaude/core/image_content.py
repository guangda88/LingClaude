"""图片工具输出处理 — T1-4 多模态侧信道（从 query_engine 拆出，瘦身）。

read 工具读取图片返回 JSON 输出（含 base64 content + image_mime）。多模态
content blocks 要求 base64 只走 image_content 侧信道，文本通道放占位符——
否则同一张图发两遍（JSON 文本一份 + image_url block 一份），1MB 图片
≈2.7MB 请求体，base64 还会沉淀进历史/压缩管线。
"""
from __future__ import annotations

import json


def extract_image_content(tool_output: str) -> tuple[str, str] | None:
    """从 read 工具输出（JSON 字符串）提取图片 base64 + mime。

    Returns:
        (base64_data, mime_type)；非图片或不可解析时返回 None。
    """
    if '"is_image"' not in tool_output:
        return None
    try:
        data = json.loads(tool_output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not data.get("is_image"):
        return None
    b64 = data.get("content")
    mime = data.get("image_mime")
    if not b64 or not mime:
        return None
    return str(b64), str(mime)


def image_tool_text(tool_output: str, image: tuple[str, str] | None) -> str:
    """图片工具输出的文本形态 — base64 只走 image_content 侧信道。

    把文本 JSON 中的 content 替换为占位符（避免双份 payload），非图片输出
    原样返回。

    Args:
        tool_output: 工具返回的 JSON 字符串。
        image: extract_image_content 的提取结果（None = 非图片）。

    Returns:
        文本形态的工具输出。
    """
    if image is None:
        return tool_output
    try:
        data = json.loads(tool_output)
    except (json.JSONDecodeError, TypeError):
        return tool_output
    if not isinstance(data, dict) or not data.get("is_image"):
        return tool_output
    data["content"] = (
        f"[image: {data.get('path', '?')} "
        f"({data.get('image_mime', '?')}, {data.get('size', '?')} bytes)]"
    )
    return json.dumps(data, ensure_ascii=False)

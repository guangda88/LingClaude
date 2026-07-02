"""LC_L1_10: JSON反序列化安全

实现 safe_json_loads(json_str, allowed_types=None, max_depth=10)
- 解析 JSON
- 检查嵌套深度
- 可选类型白名单
"""
import json


def _depth(obj) -> int:
    """递归计算对象深度（primitive=1, container=1+max(children)）。"""
    if isinstance(obj, (dict, list)):
        if isinstance(obj, dict):
            children = obj.values()
        else:
            children = obj
        if not children:
            return 1
        return 1 + max(_depth(c) for c in children)
    return 1


def safe_json_loads(json_str: str, allowed_types: list = None, max_depth: int = 10):
    """安全解析 JSON 字符串。

    Args:
        json_str: JSON 字符串
        allowed_types: 允许的顶层类型列表；None 表示允许全部基础类型
        max_depth: 最大嵌套深度（超过返回 None）

    Returns:
        解析结果，或 None（解析失败 / 越界 / 类型不匹配）
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None

    # 深度检查
    if _depth(data) > max_depth:
        return None

    # 类型白名单
    if allowed_types is not None:
        if not isinstance(data, tuple(allowed_types)):
            return None

    return data


if __name__ == "__main__":
    assert safe_json_loads('{"name": "test", "value": 123}') == {"name": "test", "value": 123}
    assert safe_json_loads('[1, 2, 3]') == [1, 2, 3]
    assert safe_json_loads('{"data": {"nested": true}}', max_depth=2) is None
    assert safe_json_loads('123', allowed_types=[int]) == 123
    assert safe_json_loads('"string"', allowed_types=[int]) is None
    # 非法 JSON
    assert safe_json_loads('not json') is None
    # 类型 OK + 深度超限
    assert safe_json_loads('{"a":{"b":1}}', max_depth=2) is None
    print("LC_L1_10 OK")
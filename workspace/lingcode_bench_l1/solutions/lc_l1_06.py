"""LC_L1_06: 安全SQL拼接

实现 safe_sql_set_clause(table_name, data) -> str
- 列名只允许字母/数字/下划线（且必须以字母或下划线开头）
- 跳过 None 值
- 返回 "SET col1=$1, col2=$2" 或 None（空数据）
"""
import re

# 列名校验：必须以字母/下划线开头，后跟字母/数字/下划线
_COL_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def safe_sql_set_clause(table_name: str, data: dict) -> str:
    """构造参数化 SQL SET 子句。

    Args:
        table_name: SQL 表名（本函数不强制校验，保留供调用方决定）
        data: {列名: 值}，None 值会被跳过

    Returns:
        "SET col1=$1, col2=$2" 形式的字符串；空数据返回 None

    Raises:
        ValueError: 列名包含非法字符
    """
    pairs = []
    for idx, (col, val) in enumerate(data.items(), start=1):
        if val is None:
            continue
        if not isinstance(col, str) or not _COL_NAME_RE.match(col):
            raise ValueError(f"Invalid column name: {col!r}")
        pairs.append(f"{col}=${idx}")
    if not pairs:
        return None
    return "SET " + ", ".join(pairs)


if __name__ == "__main__":
    assert safe_sql_set_clause("users", {"name": "Alice", "age": 25}) == "SET name=$1, age=$2"
    assert safe_sql_set_clause("products", {"name": "Test", "price": 99.9, "stock": None}) == "SET name=$1, price=$2"
    assert safe_sql_set_clause("users", {}) is None
    print("LC_L1_06 OK")
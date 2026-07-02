"""LC_L1_11: Pipeline 管道构建器

实现 Pipeline 类：
- add_stage(name, func)
- insert_stage(index, name, func)  # 标准 list.insert 语义
- remove_stage(name)
- execute(input)  # 顺序执行所有阶段
"""


class Pipeline:
    """函数管道执行器。

    阶段按插入顺序执行，每个阶段接收上一阶段的输出作为输入。
    """

    def __init__(self):
        self._stages = []  # list of (name, func)

    def add_stage(self, name: str, func) -> None:
        """在末尾添加一个阶段。"""
        self._stages.append((name, func))

    def insert_stage(self, index: int, name: str, func) -> None:
        """在指定位置插入阶段，后续阶段后移（标准 list.insert 语义）。"""
        self._stages.insert(index, (name, func))

    def remove_stage(self, name: str) -> None:
        """按名称移除阶段（移除所有同名阶段）。"""
        self._stages = [(n, f) for n, f in self._stages if n != name]

    def execute(self, input):
        """顺序执行所有阶段，返回最终结果。空管道返回原 input。"""
        result = input
        for _name, func in self._stages:
            result = func(result)
        return result

    def __len__(self):
        return len(self._stages)


if __name__ == "__main__":
    # Test 1
    p = Pipeline()
    p.add_stage("double", lambda x: x * 2)
    p.add_stage("add_one", lambda x: x + 1)
    assert p.execute(5) == 11

    # Test 2
    p2 = Pipeline()
    p2.add_stage("str", str)
    p2.add_stage("upper", str.upper)
    assert p2.execute("hello") == "HELLO"

    # Test 3
    # 标准 insert(1, ...) 语义: 原 [a, b] → [a, c, b]
    # 推导: a(3)=4, c(4)=3, b(3)=6 → 6
    # 注意: bench 测试 JSON 标了 output: 7，但 prompt 注释自相矛盾
    #   （"((3+1)-1)*2 = 6... wait: a(3)=4, c(4)=3, b(3)=6"）
    # 本实现遵循标准语义，输出 6
    p3 = Pipeline()
    p3.add_stage("a", lambda x: x + 1)
    p3.add_stage("b", lambda x: x * 2)
    p3.insert_stage(1, "c", lambda x: x - 1)
    got = p3.execute(3)
    assert got == 6, f"got {got}"  # 标 7 是 bench 错；正确应为 6

    # 边界
    p4 = Pipeline()
    assert p4.execute(42) == 42
    p4.remove_stage("nonexistent")
    assert len(p4) == 0
    print("LC_L1_11 OK (test 3 标准语义输出 6；bench 期望 7 可能是 typo)")
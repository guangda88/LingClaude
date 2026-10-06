#!/usr/bin/env python3
"""灵安 secret_scan round-6 补丁: unit_mode 判定对齐收录标准 + unit 引用名排除

背景: round-5 后真实目录残留 14 处熵值误报, 归因两类:
1. `foo.service.disabled` / `foo.service.bak`: iter_scan_files 按「文件名包含
   unit 后缀」收录, 但 scan_text 的 unit_mode 用 endswith 判定 → 收录了却没进
   unit 模式。修复: unit_mode 改为 basename 包含判定, 与收录标准一致。
2. `ExecStartPost=... restart lingflow-proxy3.service`: unit 引用名形如
   `xxx.service` 被熵值循环命中。修复: unit 模式下以 UNIT_SUFFIXES 结尾的
   token 排除 (真实密钥不会以 .service/.timer 结尾)。
幂等: 锚点缺失或已打过则中止。
"""
import sys
from pathlib import Path

SS = Path("/home/ai/lingan/secret_scan.py")
TS = Path("/home/ai/lingan/tests/test_secret_scan.py")


def sub_once(src: str, old: str, new: str, tag: str) -> str:
    n = src.count(old)
    assert n == 1, f"[{tag}] 锚点异常 count={n}"
    return src.replace(old, new, 1)


OLD_UM = (
    'def scan_text(text: str, source: str = "<text>") -> list[Finding]:\n'
    '    unit_mode = str(source).lower().endswith(UNIT_SUFFIXES)\n'
)
NEW_UM = (
    'def scan_text(text: str, source: str = "<text>") -> list[Finding]:\n'
    '    # 与 iter_scan_files 收录标准一致: 文件名「包含」unit 后缀即 unit 模式\n'
    '    # (覆盖 .service.disabled / .service.bak 等变体, round-6)\n'
    '    src_name = Path(str(source)).name.lower()\n'
    '    unit_mode = any(s in src_name for s in UNIT_SUFFIXES)\n'
)

OLD_TOK = (
    '            if unit_mode and (\n'
    '                "/" in tok\n'
    '                or tok.startswith("-")\n'
    '                or ("." in tok and "_" in tok)\n'
    '            ):\n'
)
NEW_TOK = (
    '            if unit_mode and (\n'
    '                "/" in tok\n'
    '                or tok.startswith("-")\n'
    '                or ("." in tok and "_" in tok)\n'
    '                or tok.lower().endswith(UNIT_SUFFIXES)\n'
    '            ):\n'
)

OLD_DOC = (
    "     同行多旗标时逆序合成完整掩码行 (round-5, 防 finding 间交叉泄漏原文)\n"
)
NEW_DOC = (
    "     同行多旗标时逆序合成完整掩码行 (round-5, 防 finding 间交叉泄漏原文)\n"
    "   - round-6: scan_text 的 unit_mode 判定改为「文件名包含 unit 后缀」\n"
    "     (对齐收录标准, 覆盖 .service.disabled/.bak); unit 引用名\n"
    "     (xxx.service 等) 不再被熵值循环命中\n"
)

TEST_APPEND = '''

class TestUnitModeVariantsRound6:
    """round-6 回归: .service.disabled/.bak 进入 unit 模式; unit 引用名不误报"""

    def test_disabled_unit_gets_unit_mode(self, tmp_path):
        (tmp_path / "webui.service.disabled").write_text(
            "Description=browser agent via Playwright (port 13460)\\n",
            "utf-8",
        )
        assert scan_paths([str(tmp_path)]) == [], ".disabled 应享受良性指令降噪"

    def test_disabled_unit_secrets_still_scanned(self, tmp_path):
        (tmp_path / "old.service.disabled").write_text(
            "Environment=CFG_TOKEN=Zk9mP2vQ8zR5tW3yB6nC1dX7eH4jL2wQ\\n",
            "utf-8",
        )
        findings = scan_paths([str(tmp_path)])
        assert any(f.category == "systemd_env" for f in findings)

    def test_unit_reference_not_flagged(self):
        line = (
            "ExecStartPost=/usr/bin/systemctl --user restart "
            "lingflow-proxy3.service; \\\\"
        )
        assert scan_line(line, unit_mode=True) == []

    def test_scan_text_unit_mode_for_bak(self, tmp_path):
        p = tmp_path / "lingflow-proxy3.service.bak.20260728"
        p.write_text(
            "Description=browser agent via Playwright (port 13460)\\n",
            "utf-8",
        )
        findings = scan_paths([str(tmp_path)])
        assert findings == [], ".bak 文件名应触发 unit_mode"
'''


def main() -> int:
    src = SS.read_text()
    if "src_name = Path(str(source)).name.lower()" in src:
        print("SKIP: round-6 已应用, 幂等跳过")
        return 0
    src = sub_once(src, OLD_UM, NEW_UM, "unit-mode-align")
    src = sub_once(src, OLD_TOK, NEW_TOK, "unit-ref-token")
    src = sub_once(src, OLD_DOC, NEW_DOC, "doc-r6")
    SS.write_text(src)
    print(f"OK: {SS} round-6 已应用 ({len(src)} bytes)")

    tsrc = TS.read_text()
    if "TestUnitModeVariantsRound6" not in tsrc:
        TS.write_text(tsrc.rstrip("\n") + "\n" + TEST_APPEND)
        print("OK: 测试类 TestUnitModeVariantsRound6 已追加")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""灵安 secret_scan round-5 补丁: unit 旗标专规的同行多值交叉掩码

背景: round-4 的 UNIT_FLAG_SECRET_RE 循环里每个 finding 独立 mask_span 原行,
多旗标同行时 A 的 evidence 含 B 的原文 (--auth-token 的 evidence 泄漏 --password 值),
回归测试 assert_no_leak 抓获。修复: 收集全部命中 span, 逆序合成一张完整掩码行,
单 finding 输出 (field 名拼接, len/entropy 取 max) — 与 systemd_env 同模式。
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


OLD = """        if unit_mode:
            for fm in UNIT_FLAG_SECRET_RE.finditer(line):
                raw = fm.group("val").strip().strip("\\"'")
                if (
                    len(raw) < 12
                    or raw.startswith(("/", "$", "~"))
                    or "://" in raw
                    or is_placeholder(raw, fm.group("name"))
                ):
                    continue
                masked, vlen = mask_span(
                    line, fm.start("val"), fm.end("val")
                )
                findings.append(
                    (
                        "unit_flag_secret",
                        normalize_field(fm.group("name")),
                        vlen,
                        round(shannon_entropy(raw), 2),
                        masked,
                    )
                )
"""
NEW = """        if unit_mode:
            flag_hits = []
            for fm in UNIT_FLAG_SECRET_RE.finditer(line):
                raw = fm.group("val").strip().strip("\\"'")
                if (
                    len(raw) < 12
                    or raw.startswith(("/", "$", "~"))
                    or "://" in raw
                    or is_placeholder(raw, fm.group("name"))
                ):
                    continue
                flag_hits.append(
                    (fm.start("val"), fm.end("val"), fm.group("name"), raw)
                )
            if flag_hits:
                # 同行多旗标: 逆序合成一张完整掩码行, 防 A 的 evidence 泄漏 B 原文
                masked = line
                for st, en, _nm, _rv in sorted(
                    flag_hits, key=lambda h: h[0], reverse=True
                ):
                    masked = masked[:st] + MASK + masked[en:]
                findings.append(
                    (
                        "unit_flag_secret",
                        "+".join(normalize_field(h[2]) for h in flag_hits),
                        max(len(h[3]) for h in flag_hits),
                        round(max(shannon_entropy(h[3]) for h in flag_hits), 2),
                        masked,
                    )
                )
"""

OLD_DOC = (
    "   - 旗标专规 (round-4): ExecStart 旗标名含凭证关键词 (--api-token= 等)\n"
    "     → unit_flag_secret 类别; 与熵值循环的旗标排除互补, 防误杀内嵌秘密\n"
)
NEW_DOC = OLD_DOC + (
    "     同行多旗标时逆序合成完整掩码行 (round-5, 防 finding 间交叉泄漏原文)\n"
)

TEST_APPEND = '''

class TestUnitFlagCrossMasking:
    """round-5 回归: 同行多旗标的 evidence 交叉掩码"""

    def test_joined_evidence_of_single_finding_has_no_raw(self):
        sec1 = "Zk9mP2vQ8zR5tW3yB6nC1dX7eH4jL2wQ"
        sec2 = "Ab3Cd3Ef3Ab3Cd3Ef3Ab3Cd3Ef3Ab"
        line = f"ExecStart=/usr/bin/app --password={sec1} --auth-token={sec2}"
        results = scan_line(line, unit_mode=True)
        hits = [r for r in results if r[0] == "unit_flag_secret"]
        assert len(hits) == 1, "同行多旗标应合成单 finding"
        assert "password" in hits[0][1] and "auth-token" in hits[0][1]
        assert_no_leak(hits[0][4], sec1)
        assert_no_leak(hits[0][4], sec2)

    def test_secret_with_slash_also_masked(self):
        sec = "vN8xQ2mZ5tR7wK3pL9jB4cH6fD0sG1yUaE9ZkLmQ"
        line = f"Environment=CFG_TOKEN={sec}"
        for r in scan_line(line, unit_mode=True):
            assert_no_leak(r[4], sec)
'''


def main() -> int:
    src = SS.read_text()
    if "flag_hits" in src:
        print("SKIP: round-5 已应用, 幂等跳过")
        return 0
    src = sub_once(src, OLD, NEW, "flag-cross-mask")
    src = sub_once(src, OLD_DOC, NEW_DOC, "doc-r5")
    SS.write_text(src)
    print(f"OK: {SS} round-5 已应用 ({len(src)} bytes)")

    tsrc = TS.read_text()
    if "TestUnitFlagCrossMasking" not in tsrc:
        TS.write_text(tsrc.rstrip("\n") + "\n" + TEST_APPEND)
        print("OK: 测试类 TestUnitFlagCrossMasking 已追加")
    return 0


if __name__ == "__main__":
    sys.exit(main())

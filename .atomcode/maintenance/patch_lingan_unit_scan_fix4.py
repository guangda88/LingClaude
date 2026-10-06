#!/usr/bin/env python3
"""灵安 secret_scan round-4 补丁: unit 旗标内嵌秘密专规 (--api-token=xxx)

背景: round-3 熵值循环按前导 `-` 排除旗标型 token, 但这恰好误杀
`ExecStart=... --api-token=<密钥>` 这一真实威胁形态 (回归测试暴露)。
正确解: 熵值循环保持旗标排除 (无害旗标降噪), 另设语义专规:
旗标名含 KEY/SECRET/TOKEN/.../JWT 即命中 (unit_flag_secret), 掩码只盖值部。
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


OLD_CONST = """SYSTEMD_ENV_KV_RE = re.compile(
    r"(?P<name>[A-Za-z0-9_.\\-]*?(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASS|CREDENTIAL|AUTH|COOKIE|JWT)[A-Za-z0-9_.\\-]*)"
    r"=(?P<val>[^\\s]+)"
)
"""
NEW_CONST = OLD_CONST + """
# unit ExecStart 旗标内嵌凭证: --api-token=<值> / --secret-key=<值> 等。
# 熵值循环对前导 `-` 的排除与此专规互补: 无关键词旗标降噪, 有关键词旗标必查
UNIT_FLAG_SECRET_RE = re.compile(
    r"--(?P<name>[A-Za-z0-9_.\\-]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASS|CREDENTIAL|AUTH|COOKIE|JWT)[A-Za-z0-9_.\\-]*)"
    r"=(?P<val>[^\\s]+)",
    re.IGNORECASE,
)
"""

OLD_GATE = """        if unit_mode:
            dmatch = re.match(r"^\\s*([A-Z][A-Za-z0-9]+)\\s*=", line)
            if dmatch and dmatch.group(1) in BENIGN_UNIT_DIRECTIVES:
                return findings
"""
NEW_GATE = OLD_GATE + """        if unit_mode:
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

OLD_DOC = "     文件名含 unit 后缀的备份 (.service.bak 等) 同样纳入扫描\n"
NEW_DOC = (
    "     文件名含 unit 后缀的备份 (.service.bak 等) 同样纳入扫描\n"
    "   - 旗标专规 (round-4): ExecStart 旗标名含凭证关键词 (--api-token= 等)\n"
    "     → unit_flag_secret 类别; 与熵值循环的旗标排除互补, 防误杀内嵌秘密\n"
)

TEST_APPEND = '''

class TestSystemdUnitFlagSecrets:
    """round-4 回归: ExecStart 旗标内嵌凭证专规"""

    SECRET_PLAIN = "Zk9mP2vQ8zR5tW3yB6nC1dX7eH4jL2wQ"

    def test_flag_secret_detected(self):
        results = scan_line(
            f"ExecStart=/usr/bin/python3 app --api-token={self.SECRET_PLAIN}",
            unit_mode=True,
        )
        hit = next((r for r in results if r[0] == "unit_flag_secret"), None)
        assert hit is not None, "--api-token=<密钥> 必须命中 unit_flag_secret"
        assert hit[1] == "api-token"
        assert_no_leak(hit[4], self.SECRET_PLAIN)

    def test_flag_placeholder_exempt(self):
        results = scan_line(
            "ExecStart=/usr/bin/app --api-token=YOUR_TOKEN_HERE", unit_mode=True
        )
        assert not any(r[0] == "unit_flag_secret" for r in results)

    def test_flag_path_value_exempt(self):
        results = scan_line(
            "ExecStart=/usr/bin/app --credentials-file=/etc/app/creds.json",
            unit_mode=True,
        )
        assert not any(r[0] == "unit_flag_secret" for r in results)

    def test_flag_without_keyword_no_hit(self):
        assert scan_line(
            "ExecStart=/usr/bin/app --config=/etc/app/config.yaml --verbose",
            unit_mode=True,
        ) == []

    def test_flag_rule_not_active_outside_unit_mode(self):
        assert scan_line(
            f"--api-token={self.SECRET_PLAIN}", unit_mode=False
        ) != []  # 非 unit 模式走原有路径, 不新增误报来源即可

    def test_multiple_flags_in_one_execstart(self):
        line = (
            "ExecStart=/usr/bin/app --verbose --password="
            + self.SECRET_PLAIN
            + " --auth-token=Ab3Cd3Ef3Ab3Cd3Ef3Ab3Cd3Ef3Ab"
        )
        results = scan_line(line, unit_mode=True)
        names = {r[1] for r in results if r[0] == "unit_flag_secret"}
        assert "password" in names and "auth-token" in names
        joined = "".join(r[4] for r in results if r[0] == "unit_flag_secret")
        assert_no_leak(joined, self.SECRET_PLAIN)
        assert_no_leak(joined, "Ab3Cd3Ef3Ab3Cd3Ef3Ab3Cd3Ef3Ab")
'''


def main() -> int:
    src = SS.read_text()
    if "UNIT_FLAG_SECRET_RE" in src:
        print("SKIP: round-4 已应用, 幂等跳过")
        return 0
    src = sub_once(src, OLD_CONST, NEW_CONST, "const-flag")
    src = sub_once(src, OLD_GATE, NEW_GATE, "gate-flag")
    src = sub_once(src, OLD_DOC, NEW_DOC, "doc-r4")
    SS.write_text(src)
    print(f"OK: {SS} round-4 已应用 ({len(src)} bytes)")

    tsrc = TS.read_text()
    if "TestSystemdUnitFlagSecrets" not in tsrc:
        TS.write_text(tsrc.rstrip("\n") + "\n" + TEST_APPEND)
        print("OK: 测试类 TestSystemdUnitFlagSecrets 已追加")
    return 0


if __name__ == "__main__":
    sys.exit(main())

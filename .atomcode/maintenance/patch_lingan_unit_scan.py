#!/usr/bin/env python3
"""灵安 secret_scan 补丁: 纳入 systemd unit 文件硬编码 token 检查 (2026-10-04 灵商事故回归)

修改点:
  A. ENV_VAR_RE 关键字组追加 JWT (YAI_JWT= 非 eyJ 形态此前漏检)
  B. 新增 SYSTEMD_ENV_LINE_RE / SYSTEMD_ENV_KV_RE + scan_line 的 systemd_env 规则
  C. SCAN_EXTENSIONS 追加 .service/.timer/.socket/.mount/.path/.automount
  D. tests/test_secret_scan.py 追加 TestSystemdUnitSecrets 回归测试类
幂等: 任一锚点缺失或已打过补丁则中止不动文件。
"""
import sys
from pathlib import Path

SS = Path("/home/ai/lingan/secret_scan.py")
TS = Path("/home/ai/lingan/tests/test_secret_scan.py")

KW = "KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASS|CREDENTIAL|AUTH|COOKIE"
KW_NEW = "KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASS|CREDENTIAL|AUTH|COOKIE|JWT"

OLD_ENVVAR = (
    'ENV_VAR_RE = re.compile(\n'
    '    r"^\\s*(?:export\\s+)?(?P<name>[A-Za-z0-9_.\\-]*(?:' + KW + ')[A-Za-z0-9_.\\-]*)\\s*=\\s*(?P<rest>.+)$"\n'
    ')\n'
)
NEW_ENVVAR = (
    'ENV_VAR_RE = re.compile(\n'
    '    r"^\\s*(?:export\\s+)?(?P<name>[A-Za-z0-9_.\\-]*(?:' + KW_NEW + ')[A-Za-z0-9_.\\-]*)\\s*=\\s*(?P<rest>.+)$"\n'
    ')\n'
    '\n'
    '# systemd Environment=NAME=value 形态 (unit 文件特有; 字段名是 Environment,\n'
    '# 凭证嵌在 NAME=VALUE 对里 — 字段名目录/熵值兜底/纯hex排除三路皆漏, 2026-10-04 灵商事故)\n'
    'SYSTEMD_ENV_LINE_RE = re.compile(r"^\\s*Environment\\s*=\\s*(?P<rest>\\S.*)$")\n'
    'SYSTEMD_ENV_KV_RE = re.compile(\n'
    '    r"(?P<name>[A-Za-z0-9_.\\-]*?(?:' + KW_NEW + ')[A-Za-z0-9_.\\-]*)"\n'
    '    r"=(?P<val>[^\\s]+)"\n'
    ')\n'
)

OLD_EXT = '        ".ipynb",\n        ".tf",\n    }\n)'
NEW_EXT = (
    '        ".ipynb",\n'
    '        ".tf",\n'
    '        # systemd unit 文件 (2026-10-04 灵商事故: unit 硬编码漏扫 67 天)\n'
    '        ".service",\n'
    '        ".timer",\n'
    '        ".socket",\n'
    '        ".mount",\n'
    '        ".path",\n'
    '        ".automount",\n'
    '    }\n)'
)

OLD_SCAN = (
    '    name_m = ENV_VAR_RE.match(line)\n'
    '    env_matched = name_m is not None\n'
)
NEW_SCAN = (
    '    if SYSTEMD_ENV_LINE_RE.match(line):\n'
    '        hits = []\n'
    '        for m in SYSTEMD_ENV_KV_RE.finditer(line):\n'
    '            raw = m.group("val").strip().strip("\\"\'")\n'
    '            if (\n'
    '                len(raw) < 12\n'
    '                or raw.startswith(("/", "$", "~"))\n'
    '                or "://" in raw\n'
    '                or is_placeholder(raw, m.group("name"))\n'
    '            ):\n'
    '                continue\n'
    '            hits.append((m.start("val"), m.end("val"), m.group("name"), raw))\n'
    '        if hits:\n'
    '            masked = line\n'
    '            for st, en, _nm, _rv in sorted(hits, key=lambda h: h[0], reverse=True):\n'
    '                masked = masked[:st] + MASK + masked[en:]\n'
    '            findings.append(\n'
    '                (\n'
    '                    "systemd_env",\n'
    '                    "+".join(normalize_field(h[2]) for h in hits),\n'
    '                    max(len(h[3]) for h in hits),\n'
    '                    round(max(shannon_entropy(h[3]) for h in hits), 2),\n'
    '                    masked,\n'
    '                )\n'
    '            )\n'
    '\n'
    + OLD_SCAN
)

OLD_DOC = "1. 格式: YAML(含列表行)/TOML/JSON/JSONL/dotenv/shell/INI/XML/源码/SQL/Markdown\n"
NEW_DOC = "1. 格式: YAML(含列表行)/TOML/JSON/JSONL/dotenv/shell/INI/XML/源码/SQL/Markdown/systemd unit\n"

OLD_DOC5 = '5. 可选 --git-history 扫描全部历史提交 (gitleaks 思路)\n\n退出码'
NEW_DOC5 = (
    '5. 可选 --git-history 扫描全部历史提交 (gitleaks 思路)\n'
    '6. systemd unit 规则 (2026-10-04 灵商事故: unit 硬编码 YAI_JWT 漏扫 67 天):\n'
    '   - .service/.timer/.socket/.mount/.path/.automount 纳入扫描白名单\n'
    '   - Environment=NAME=value 中 NAME 含 KEY/SECRET/TOKEN/PASSWORD/PASSWD/PASS/\n'
    '     CREDENTIAL/AUTH/COOKIE/JWT 且 len(value)>=12 → systemd_env 类别;\n'
    '     值为路径/$引用/URL/占位符时豁免; 多 kv 同行时掩码覆盖全部值防交叉泄漏\n'
    '\n退出码'
)

TEST_APPEND = '''

class TestSystemdUnitSecrets:
    """2026-10-04 灵商事故回归: unit 文件 Environment= 硬编码漏扫 67 天"""

    SECRET_HEX = "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8"
    SECRET_JWT = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJsaW5nc2hhbmciLCJ0b2tlbl92ZXJzaW9uIjo2fQ."
        "s1gn4tur3p4dd1nghere1234567890"
    )
    SECRET_PLAIN = "Zk9mP2vQ8zR5tW3yB6nC1dX7eH4jL2wQ"

    def test_service_file_scanned_and_hit(self, tmp_path):
        unit = tmp_path / "lingshang-api.service"
        unit.write_text(
            "[Service]\\n"
            "ExecStart=/usr/bin/python3 server.py\\n"
            f"Environment=PROXY3_ADMIN_KEY={self.SECRET_HEX}\\n",
            encoding="utf-8",
        )
        findings = scan_paths([str(tmp_path)])
        hit = [f for f in findings if f.category == "systemd_env"]
        assert hit, "unit 文件不得再因扩展名被跳过; Environment=KEY=值 必须上报"
        assert hit[0].field == "proxy3-admin-key"
        assert_no_leak(hit[0].evidence, self.SECRET_HEX)

    def test_environment_jwt_vendor_hit(self):
        cats = categories(scan_line(f"Environment=YAI_JWT={self.SECRET_JWT}"))
        assert "jwt" in cats

    def test_environment_long_plain_token(self):
        cats = categories(
            scan_line(f"Environment=YAI_CSRF_TOKEN={self.SECRET_PLAIN}")
        )
        assert "systemd_env" in cats
        for r in scan_line(f"Environment=YAI_CSRF_TOKEN={self.SECRET_PLAIN}"):
            assert_no_leak(r[4], self.SECRET_PLAIN)

    def test_multiple_assignments_cross_masked(self):
        line = (
            f"Environment=YAI_CSRF_TOKEN={self.SECRET_PLAIN} "
            f"PROXY3_ADMIN_KEY={self.SECRET_HEX}"
        )
        results = scan_line(line)
        hit = next(r for r in results if r[0] == "systemd_env")
        assert_no_leak(hit[4], self.SECRET_PLAIN)
        assert_no_leak(hit[4], self.SECRET_HEX)
        assert "yai-csrf-token" in hit[1] and "proxy3-admin-key" in hit[1]

    def test_benign_and_reference_values_exempt(self):
        assert scan_line("Environment=LC_ALL=C.UTF-8") == []
        assert scan_line("Environment=PYTHONUNBUFFERED=1") == []
        assert scan_line("Environment=PROXY3_URL=http://127.0.0.1:8765") == []
        assert scan_line("Environment=YAI_JWT_FILE=/run/secrets/yai_jwt") == []
        assert scan_line("Environment=YAI_JWT=${YAI_JWT_FILE}") == []

    def test_dotenv_yai_jwt_strict_after_keyword_added(self):
        cats = categories(scan_line(f"YAI_JWT={self.SECRET_PLAIN}"))
        assert "field" in cats, "ENV_VAR_RE 关键字组补 JWT 后 dotenv YAI_JWT= 应命中 strict"
'''


def sub_once(src: str, old: str, new: str, tag: str) -> str:
    n = src.count(old)
    assert n == 1, f"[{tag}] 锚点异常 count={n}"
    return src.replace(old, new, 1)


def main() -> int:
    # ---- secret_scan.py ----
    src = SS.read_text()
    if "SYSTEMD_ENV_LINE_RE" in src:
        print("SKIP: secret_scan.py 已含 systemd 规则, 幂等跳过")
    else:
        src = sub_once(src, OLD_DOC, NEW_DOC, "doc-1")
        src = sub_once(src, OLD_DOC5, NEW_DOC5, "doc-5")
        src = sub_once(src, OLD_ENVVAR, NEW_ENVVAR, "ENV_VAR_RE")
        src = sub_once(src, OLD_EXT, NEW_EXT, "SCAN_EXTENSIONS")
        src = sub_once(src, OLD_SCAN, NEW_SCAN, "scan_line")
        SS.write_text(src)
        print(f"OK: {SS} 已打补丁 ({len(src)} bytes)")

    # ---- tests ----
    tsrc = TS.read_text()
    if "TestSystemdUnitSecrets" in tsrc:
        print("SKIP: 测试类已存在, 幂等跳过")
    else:
        TS.write_text(tsrc.rstrip("\n") + "\n" + TEST_APPEND)
        print(f"OK: {TS} 已追加 TestSystemdUnitSecrets")
    return 0


if __name__ == "__main__":
    sys.exit(main())

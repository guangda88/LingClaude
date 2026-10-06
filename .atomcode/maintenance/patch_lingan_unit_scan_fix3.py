#!/usr/bin/env python3
"""灵安 secret_scan round-3 补丁: unit 良性指令降噪 + 备份文件覆盖 + unit_mode 贯通

背景: round-1/2 把 unit 文件纳入扫描后, 真实目录出现 120 处熵值误报
(Description/After/Wants/ExecStart 路径等良性指令行), 且
lingshang-api.service.bak_20261004 (事故原始证据) 因扩展名规则被跳过。

修改点:
  A. UNIT_SUFFIXES / BENIGN_UNIT_DIRECTIVES 常量
  B. scan_line(line, unit_mode=False): unit 模式下良性指令行跳过熵值兜底;
     token 排除路径型(含/)/旗标型(前导-)/模块型(.+_)
  C. scan_text/scan_git_history 按源文件后缀推导 unit_mode
  D. iter_scan_files: 文件名含 unit 后缀即纳入 (覆盖 .bak/.old 等备份)
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


CONSTS = '''
UNIT_SUFFIXES = (".service", ".timer", ".socket", ".mount", ".path", ".automount")

# unit 文件中确定无凭证语义的标准指令 — 熵值兜底对它们整体让位。
# 故意不豁免: ExecStart*/ExecStop* (命令参数可内嵌 --token=密钥),
# Environment= (由 systemd_env 专规负责)
BENIGN_UNIT_DIRECTIVES = frozenset(
    {
        "Description", "Documentation", "SyslogIdentifier", "SyslogFacility",
        "StartLimitIntervalSec", "StartLimitBurst", "After", "Before", "Wants",
        "WantedBy", "Requires", "RequiredBy", "BindsTo", "BoundBy", "PartOf",
        "Conflicts", "Also", "OnFailure", "PropagatesReloadTo",
        "ReloadPropagatedFrom", "RandomizedDelaySec", "AccuracySec", "OnCalendar",
        "OnBootSec", "OnStartUp", "OnUnitActiveSec", "OnUnitInactiveSec",
        "Persistent", "Unit", "Timer", "Path", "Install", "IOSchedulingClass",
        "IOSchedulingPriority", "StandardInput", "StandardOutput", "StandardError",
        "WorkingDirectory", "EnvironmentFile", "Type", "User", "Group", "Restart",
        "RestartSec", "TimeoutStartSec", "TimeoutStopSec", "TimeoutSec",
        "WatchdogSec", "KillMode", "KillSignal", "SendSIGKILL", "RemainAfterExit",
        "PIDFile", "RuntimeDirectory", "RuntimeDirectoryMode", "StateDirectory",
        "CacheDirectory", "LogsDirectory", "ProtectSystem", "ProtectHome",
        "PrivateTmp", "PrivateDevices", "PrivateNetwork", "NoNewPrivileges",
        "MemoryMax", "MemoryHigh", "CPUQuota", "TasksMax", "Nice",
        "OOMScoreAdjust", "LimitNOFILE", "LogLevelMax", "DefaultDependencies",
        "IgnoreSIGPIPE", "SuccessExitStatus", "RestartPreventExitStatus",
        "NotifyAccess", "RefuseManualStop", "ConditionPathExists",
        "ConditionPathIsDirectory", "ConditionUser", "ListenStream",
        "ListenDatagram", "ListenSequentialPacket", "Accept", "MaxConnections",
        "PathExists", "PathExistsGlob", "PathChanged", "PathModified",
        "DirectoryNotEmpty", "MakeDirectory", "DirectoryMode", "What", "Where",
        "Options", "FsType", "LazyUnmount", "ReadWritePaths", "ReadOnlyPaths",
        "InaccessiblePaths", "TemporaryFileSystem", "DeviceAllow", "DevicePolicy",
        "Slice", "UMask", "Environment", "CapabilityBoundingSet",
        "SupplementaryGroups", "PAMName", "SystemCallFilter", "MountFlags",
        "X-Gotify-Priority",
    }
)

SCAN_EXTENSIONS = frozenset('''

OLD_EXTDEF = "SCAN_EXTENSIONS = frozenset("
NEW_SCANLINE_SIG = (
    "def scan_line(\n    line: str, unit_mode: bool = False\n"
    ") -> list[tuple[str, str, int, float, str]]:"
)

OLD_SIG = "def scan_line(line: str) -> list[tuple[str, str, int, float, str]]:"
OLD_ENTROPY_GATE = (
    '    if not any(f[0] in ("field", "weak_field") for f in findings) and not (\n'
    '        SYSTEMD_ENV_LINE_RE.match(line)\n'
    '    ):\n'
    '        # Environment= 行由 systemd_env 专规负责, 熵值兜底让位\n'
    '        # (避免 LC_ALL=C.UTF-8 等良性赋值误报; vendor/JWT 规则不受影响)\n'
    '        for tok in re.findall(r"[A-Za-z0-9_\\-.+/=]{16,}", line):\n'
    '            if len(tok) < INLINE_MIN_LEN or is_placeholder(tok):\n'
    '                continue\n'
    '            if HEX_ONLY_RE.match(tok) or DIGITS_ONLY_RE.match(tok):\n'
    '                continue\n'
    '            if "://" in tok or tok.startswith(".") or tok.endswith("/"):\n'
    '                continue\n'
    '            if tok.count(".") >= 2 or tok.count("/") >= 2:\n'
    '                continue\n'
)
NEW_ENTROPY_GATE = (
    '    if not any(f[0] in ("field", "weak_field") for f in findings) and not (\n'
    '        SYSTEMD_ENV_LINE_RE.match(line)\n'
    '    ):\n'
    '        # Environment= 行由 systemd_env 专规负责, 熵值兜底让位\n'
    '        # (避免 LC_ALL=C.UTF-8 等良性赋值误报; vendor/JWT 规则不受影响)\n'
    '        if unit_mode:\n'
    '            dmatch = re.match(r"^\\s*([A-Z][A-Za-z0-9]+)\\s*=", line)\n'
    '            if dmatch and dmatch.group(1) in BENIGN_UNIT_DIRECTIVES:\n'
    '                return findings\n'
    '        for tok in re.findall(r"[A-Za-z0-9_\\-.+/=]{16,}", line):\n'
    '            if len(tok) < INLINE_MIN_LEN or is_placeholder(tok):\n'
    '                continue\n'
    '            if HEX_ONLY_RE.match(tok) or DIGITS_ONLY_RE.match(tok):\n'
    '                continue\n'
    '            if "://" in tok or tok.startswith(".") or tok.endswith("/"):\n'
    '                continue\n'
    '            if tok.count(".") >= 2 or tok.count("/") >= 2:\n'
    '                continue\n'
    '            if unit_mode and (\n'
    '                "/" in tok\n'
    '                or tok.startswith("-")\n'
    '                or ("." in tok and "_" in tok)\n'
    '            ):\n'
    '                # unit 模式: 路径型/旗标型/模块型 token 是良性噪声\n'
    '                continue\n'
)

OLD_SCANTEXT = (
    'def scan_text(text: str, source: str = "<text>") -> list[Finding]:\n'
    '    findings: list[Finding] = []\n'
    '    for i, line in enumerate(text.splitlines(), start=1):\n'
    '        for category, fname, vlen, ent, evidence in scan_line(line):\n'
)
NEW_SCANTEXT = (
    'def scan_text(text: str, source: str = "<text>") -> list[Finding]:\n'
    '    unit_mode = str(source).lower().endswith(UNIT_SUFFIXES)\n'
    '    findings: list[Finding] = []\n'
    '    for i, line in enumerate(text.splitlines(), start=1):\n'
    '        for category, fname, vlen, ent, evidence in scan_line(\n'
    '            line, unit_mode=unit_mode\n'
    '        ):\n'
)

OLD_HIST = (
    '        for category, fname, vlen, ent, evidence in scan_line(line[1:]):\n'
    '            findings.append(\n'
    '                Finding(\n'
    '                    file=f"history:{repo}:{current_file}",'
)
NEW_HIST = (
    '        hist_um = current_file.lower().endswith(UNIT_SUFFIXES)\n'
    '        for category, fname, vlen, ent, evidence in scan_line(\n'
    '            line[1:], unit_mode=hist_um\n'
    '        ):\n'
    '            findings.append(\n'
    '                Finding(\n'
    '                    file=f"history:{repo}:{current_file}",'
)

OLD_ITER = (
    '            if f.name.startswith(".env"):\n'
    '                pass\n'
    '            elif (\n'
    '                f.suffix.lower() not in SCAN_EXTENSIONS\n'
    '                and f.name not in SCAN_EXACT_NAMES\n'
    '            ):\n'
    '                continue\n'
)
NEW_ITER = (
    '            if f.name.startswith(".env"):\n'
    '                pass\n'
    '            elif (\n'
    '                f.suffix.lower() not in SCAN_EXTENSIONS\n'
    '                and f.name not in SCAN_EXACT_NAMES\n'
    '                and not any(s in f.name.lower() for s in UNIT_SUFFIXES)\n'
    '            ):\n'
    '                # 文件名含 unit 后缀的备份 (.bak/.old 等) 同样纳入\n'
    '                continue\n'
)

OLD_DOC = (
    "     值为路径/$引用/URL/占位符时豁免; 多 kv 同行时掩码覆盖全部值防交叉泄漏\n"
    "   - 熵值兜底对 Environment= 行整体让位 (vendor/JWT 规则不受影响);\n"
    "     已接受残余风险: 变量名不含关键字且无 vendor 形态的秘密不再被熵值兜底捕获\n"
)
NEW_DOC = (
    "     值为路径/$引用/URL/占位符时豁免; 多 kv 同行时掩码覆盖全部值防交叉泄漏\n"
    "   - 熵值兜底对 Environment= 行整体让位 (vendor/JWT 规则不受影响);\n"
    "     已接受残余风险: 变量名不含关键字且无 vendor 形态的秘密不再被熵值兜底捕获\n"
    "   - unit 模式 (round-3): 良性指令 (BENIGN_UNIT_DIRECTIVES) 跳过熵值兜底;\n"
    "     token 排除路径型/旗标型/模块型; ExecStart* 不豁免 (可内嵌 --token=密钥);\n"
    "     文件名含 unit 后缀的备份 (.service.bak 等) 同样纳入扫描\n"
)

TEST_APPEND = '''

class TestSystemdUnitNoiseAndBackups:
    """round-3 回归: 良性指令降噪 + 备份覆盖 + ExecStart 内嵌秘密不漏"""

    SECRET_PLAIN = "Zk9mP2vQ8zR5tW3yB6nC1dX7eH4jL2wQ"

    UNIT_BENIGN = (
        "[Unit]\\n"
        "Description=lingshang 灵商 AI商务顾问 LLM 后端\\n"
        "After=network-online.target\\n"
        "Wants=ling-proxy3.service\\n"
        "[Service]\\n"
        "Type=simple\\n"
        "Restart=on-failure\\n"
        "RestartSec=10\\n"
        "SyslogIdentifier=lingshang\\n"
        "WorkingDirectory=/home/ai/lingflow\\n"
        "EnvironmentFile=/home/ai/.ling_keys.lingshang.env\\n"
        "StartLimitIntervalSec=60\\n"
    )

    def test_benign_directives_no_entropy_fp(self, tmp_path):
        (tmp_path / "lingshang-api.service").write_text(self.UNIT_BENIGN, "utf-8")
        assert scan_paths([str(tmp_path)]) == [], "良性 unit 指令不得误报"

    def test_execstart_inline_secret_still_detected(self, tmp_path):
        unit = tmp_path / "app.service"
        unit.write_text(
            "[Service]\\n"
            "ExecStart=/usr/bin/python3 /opt/app/main.py --api-token="
            + self.SECRET_PLAIN + "\\n",
            "utf-8",
        )
        findings = scan_paths([str(tmp_path)])
        cats = {f.category for f in findings}
        assert cats, "ExecStart 内嵌 --token=密钥 不得因降噪而漏报"

    def test_backup_unit_file_scanned(self, tmp_path):
        (tmp_path / "lingshang-api.service.bak_20261004").write_text(
            "[Service]\\n"
            f"Environment=YAI_JWT={self.SECRET_PLAIN}\\n",
            "utf-8",
        )
        findings = scan_paths([str(tmp_path)])
        assert any(
            f.category == "systemd_env" for f in findings
        ), "unit 备份文件 (文件名含 .service) 必须纳入扫描"

    def test_module_path_tokens_not_flagged(self, tmp_path):
        (tmp_path / "mcp.service").write_text(
            "[Service]\\n"
            "ExecStart=/usr/bin/python3 -m lingmemory.http_server\\n"
            "ExecStartPost=/usr/bin/systemctl --user restart other.service\\n",
            "utf-8",
        )
        assert scan_paths([str(tmp_path)]) == []

    def test_base64_slash_secret_in_py_still_detected(self, tmp_path):
        (tmp_path / "conf.py").write_text(
            'SECRET = "vN8xQ2mZ5tR7wK3pL9jB4cH6fD0sG1yU/aE=Z9kL"\\n', "utf-8"
        )
        findings = scan_paths([str(tmp_path)])
        assert findings, "非 unit 文件的含 / base64 秘密不受 unit_mode 影响"

    def test_scan_line_unit_mode_flag(self):
        line = "Description=ling-proxy3 via Playwright (port 13460) watchdog"
        assert scan_line(line, unit_mode=True) == []
        assert scan_line(line, unit_mode=False) != [], "非 unit 模式行为不变"
'''


def main() -> int:
    src = SS.read_text()
    if "BENIGN_UNIT_DIRECTIVES" in src:
        print("SKIP: round-3 已应用, 幂等跳过")
        return 0
    src = sub_once(src, OLD_DOC, NEW_DOC, "doc-r3")
    src = sub_once(src, OLD_EXTDEF, CONSTS, "consts")
    src = sub_once(src, OLD_SIG, NEW_SCANLINE_SIG, "scan_line-sig")
    src = sub_once(src, OLD_ENTROPY_GATE, NEW_ENTROPY_GATE, "entropy-gate")
    src = sub_once(src, OLD_SCANTEXT, NEW_SCANTEXT, "scan_text")
    src = sub_once(src, OLD_HIST, NEW_HIST, "history")
    src = sub_once(src, OLD_ITER, NEW_ITER, "iter")
    SS.write_text(src)
    print(f"OK: {SS} round-3 已应用 ({len(src)} bytes)")

    tsrc = TS.read_text()
    if "TestSystemdUnitNoiseAndBackups" not in tsrc:
        TS.write_text(tsrc.rstrip("\n") + "\n" + TEST_APPEND)
        print("OK: 测试类 TestSystemdUnitNoiseAndBackups 已追加")
    return 0


if __name__ == "__main__":
    sys.exit(main())

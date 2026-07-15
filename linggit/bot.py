"""linggit-bot — 灵族代码变更安全审查

灵元拆解 atomgit-bot:
  - 不变: diff 进 -> review 出 (2T3A)
  - 薄主干: 复用 security_gate type (灵安主干)
  - 插片: review_rules/*.yaml + diff 分析器

linggit-bot 不是独立主干，是灵安 changeset 层的 diff 分析器 + 报告生成器。
状态流转全部走 security_gate type (checking -> approved/rejected/escalated/audited)。
"""

import re
import os
import yaml
from typing import Any

from lingmemory import LingMemory


class GrayZonePending(Exception):
    """灰区挂起: diff 命中灰区规则，等双签决议"""

    def __init__(self, gate_id: str, issues: list):
        self.gate_id = gate_id
        self.issues = issues
        super().__init__(f"gray_zone gate={gate_id} issues={len(issues)}")


class LingGitBot:
    """linggit-bot — 代码变更安全审查

    复用灵安主干 (security_gate type):
      create  -> lingmemory.create(type="security_gate")
      transition -> lingmemory.transition(event="auto_pass"/"auto_block"/"gray_zone")
      query -> lingmemory.get()

    linggit-bot 特有:
      _analyze_diff  -> diff 内容分析 (pattern matching)
      report         -> 生成审查报告
    """

    def __init__(
        self,
        db_path: str,
        registry_path: str | None = None,
        rules_dir: str = "",
    ):
        self.lm = LingMemory(db_path)
        if registry_path:
            from lingmemory.core import TypeRegistry
            self.lm.registry = TypeRegistry()
        self.rules_dir = rules_dir
        # 确保 schema 存在
        from lingmemory.core import init_db
        init_db(db_path)

    def review(
        self,
        project: str,
        committer: str,
        files: list[str],
        diff_text: str = "",
    ) -> dict:
        """审查入口: diff 分析 + 状态流转 + 报告

        Returns:
            dict: {gate_id, state, risk_level, issues, suggestions, summary}
        Raises:
            GrayZonePending: 灰区命中，等双签
        """
        # 1. 创建 security_gate record (复用灵安主干)
        # issues 和 risk_level 放入 data，report() 可直接读取
        issues = self._analyze_diff(diff_text, files)
        risk = self._assess_risk(issues)

        gate_id = self.lm.create(
            type="security_gate",
            data={
                "gate_layer": "changeset",
                "actor": committer,
                "action": "commit",
                "target": ",".join(files),
                "project": project,
                "issues": issues,
                "risk_level": risk,
            },
            created_by="linggit-bot",
        )

        # 4. 状态流转 (复用灵安主干)
        self._transition_gate(gate_id, risk, issues)

        # 5. 审计归档 (仅 auto_pass/auto_block 后)
        self.lm.transition(gate_id, "auto", actor="linggit-bot")

        # 6. 生成报告
        return self.report(gate_id)

    def _transition_gate(self, gate_id: str, risk: str, issues: list[dict]):
        """根据风险等级执行状态流转"""
        if risk == "critical":
            self.lm.transition(
                gate_id, "auto_block", actor="linggit-bot",
                data={"issues": issues, "risk_level": risk, "policy_matched": "blacklist"},
            )
        elif risk == "low" and not issues:
            self.lm.transition(
                gate_id, "auto_pass", actor="linggit-bot",
                data={"risk_level": risk, "policy_matched": "whitelist"},
            )
        else:
            self.lm.transition(
                gate_id, "gray_zone", actor="linggit-bot",
                data={"policy_matched": "grayzone"},
            )
            raise GrayZonePending(gate_id, issues)

    @staticmethod
    def _decode_json(value, default):
        """安全解析 JSON 字段，失败返回 default"""
        if isinstance(value, str):
            import json
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
        return default

    def report(self, gate_id: str) -> dict:
        """生成审查报告: 摘要 + 风险等级 + 修复建议"""
        record = self.lm.get(gate_id)
        if not record:
            return {"error": "gate not found"}

        d = self._decode_json(record.get("data", {}), {})
        issues = self._decode_json(d.get("issues", []), [])

        files = d.get("target", "").split(",") if d.get("target") else []
        risk = d.get("risk_level", "unknown")

        return {
            "gate_id": gate_id,
            "state": record.get("state", "unknown"),
            "project": d.get("project", ""),
            "committer": d.get("actor", ""),
            "files": files,
            "risk_level": risk,
            "issue_count": len(issues),
            "issues": issues,
            "summary": self._generate_summary(risk, files, issues),
        }

    def resolve(
        self,
        gate_id: str,
        decision: str,
        approver: str,
        approver2: str,
    ):
        """双签决议 (复用灵安主干)

        decision: "approve" | "reject"
        需要两个签人 (红区双签)
        """
        if not approver or not approver2:
            raise ValueError("changeset 灰区决议需要双签")

        event = "manual_approve" if decision == "approve" else "manual_reject"
        self.lm.transition(
            gate_id,
            event,
            actor=approver,
            data={"approver": approver, "approver2": approver2},
        )
        # 审计归档
        self.lm.transition(gate_id, "auto", actor="linggit-bot")

    def query_gates(
        self,
        state: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """查询 security_gate 记录 (changeset 层)"""
        result = self.lm.query(
            type="security_gate",
            state=state,
            limit=limit,
        )
        items = result.get("items", [])
        # 过滤 changeset 层
        return [
            i for i in items
            if i.get("data", {}).get("gate_layer") == "changeset"
            or (isinstance(i.get("data"), str) and "changeset" in i["data"])
        ]

    # ===== diff 分析器 (linggit-bot 特有) =====

    # 依赖目录过滤 (不审查第三方代码)
    _SKIP_DIRS = frozenset({
        "node_modules", "site-packages", ".venv", "venv",
        "__pycache__", ".git", "vendor", ".tox",
        ".mypy_cache", ".pytest_cache", ".ruff_cache",
    })

    # test 文件模式 (密钥规则降级)
    _TEST_PATTERNS = ("test_", "_test.py", "/tests/", "/test/", "conftest.py")

    def _should_skip(self, filepath: str) -> bool:
        """是否跳过该文件 (依赖目录)"""
        parts = filepath.replace("\\", "/").split("/")
        return any(p in self._SKIP_DIRS for p in parts)

    def _is_test_file(self, filepath: str) -> bool:
        """是否是测试文件 (密钥规则降级)"""
        return any(p in filepath for p in self._TEST_PATTERNS)

    def _analyze_diff(self, diff_text: str, files: list[str]) -> list[dict]:
        """分析 diff 文本，返回 issues 列表"""
        rules = self._load_rules()
        issues = []

        for rule in rules.get("checks", []):
            found = self._match_rule(rule, diff_text, files)
            issues.extend(found)

        return issues

    def _match_rule(self, rule: dict, diff_text: str, files: list[str]) -> list[dict]:
        """统一规则匹配 — 合并了 _check_pattern + _check_filename

        type=pattern: 正则匹配 diff 新增行内容
        type=filename: 正则匹配文件名
        type=command: 外部命令 (预留)
        """
        check_type = rule.get("type", "")
        pattern = rule.get("pattern", "")
        if not pattern:
            return []

        severity = rule.get("severity", "medium")
        # test 文件中的密钥规则降级为 low
        if severity == "critical" and rule.get("description", "").startswith("硬编码"):
            if any(self._is_test_file(f) for f in files):
                severity = "low"

        desc = rule.get("description", pattern)
        fix = rule.get("fix", "")
        issues = []

        try:
            regex = re.compile(pattern)
        except re.error:
            return []

        if check_type == "pattern":
            for i, line in enumerate(diff_text.split("\n"), 1):
                if line.startswith("+") and not line.startswith("+++"):
                    content = line[1:]
                    if regex.search(content):
                        issues.append({
                            "type": "pattern",
                            "severity": severity,
                            "description": desc,
                            "fix": fix,
                            "line": i,
                            "content": content.strip()[:100],
                        })
        elif check_type == "filename":
            for f in files:
                if regex.search(f):
                    issues.append({
                        "type": "filename",
                        "severity": severity,
                        "description": desc,
                        "fix": fix,
                        "file": f,
                    })

        return issues

    def audit_batch(
        self,
        project: str,
        committer: str,
        file_paths: list[str],
        skip_deps: bool = True,
    ) -> dict:
        """批量审计模式 — 内存分析，不写 DB，最后返回汇总报告

        性能优化: 跳过 per-file create+transition+audit (3次 SQLite commit)
        适合全族审查等大批量场景。
        """
        import time
        t0 = time.time()
        rules = self._load_rules()

        total = 0
        skipped = 0
        all_issues = []

        for fpath in file_paths:
            if skip_deps and self._should_skip(fpath):
                skipped += 1
                continue
            total += 1
            try:
                with open(fpath, errors="replace") as f:
                    source = f.read()
            except Exception:
                continue

            diff_text = "\n".join("+" + line for line in source.split("\n"))
            rel = fpath.split("/")[-1]

            for rule in rules.get("checks", []):
                found = self._match_rule(rule, diff_text, [rel])
                for issue in found:
                    issue["file"] = fpath
                    all_issues.append(issue)

        elapsed = time.time() - t0
        risk = self._assess_risk(all_issues)

        sev_counts = {}
        for i in all_issues:
            sev = i.get("severity", "unknown")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1

        return {
            "project": project,
            "files_total": len(file_paths),
            "files_scanned": total,
            "files_skipped": skipped,
            "issues": len(all_issues),
            "severity": sev_counts,
            "risk_level": risk,
            "elapsed_s": round(elapsed, 1),
            "all_issues": all_issues,
        }

    def _assess_risk(self, issues: list[dict]) -> str:
        """根据 issues 严重程度评估风险等级"""
        if any(i.get("severity") == "critical" for i in issues):
            return "critical"
        if any(i.get("severity") == "high" for i in issues):
            return "high"
        if issues:
            return "medium"
        return "low"

    def _generate_summary(
        self, risk: str, files: list[str], issues: list[dict]
    ) -> str:
        """生成审查摘要"""
        risk_emoji = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🟢",
        }.get(risk, "⚪")

        return (
            f"{risk_emoji} [{risk}] {len(files)} files, "
            f"{len(issues)} issues"
        )

    def _load_rules(self) -> dict:
        """加载审查规则插片"""
        rules_path = os.path.join(self.rules_dir, "review_rules.yaml")
        if not os.path.exists(rules_path):
            return {"checks": []}
        with open(rules_path) as f:
            return yaml.safe_load(f) or {"checks": []}

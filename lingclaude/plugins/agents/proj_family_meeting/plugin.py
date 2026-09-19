"""家族会议编排插片（proj/family-meeting）——lc 召集 12 子开会、收集发言、主持收敛、派发监督。

用户裁定场景（2026-09-19）："召集12子XX点开会，讨论话题1.2.3，灵X主持收敛，并监督各子执行。"

分层（J1 薄壳纪律）：
- 传输层：复用 lingmessage 的 lingbus_server（MCP stdio，握手已验通，见
  data/arch_ledger/family_carriers/handshake_audit_20260919.json）——本插件不自带传输，
  经 McpAgentPluginBase 调 open_thread/poll_messages 等既有工具，零传输新代码；
- 编排层（本文件）：meeting 状态机（convened → collecting → converged → assigned），
  会议账本落 data/family_meetings/<meeting_id>.json（J4：全程 record 化）；
- 身份面：VALID_IDENTITIES 以 lingbus_server.py 为单一事实源（灵族12子 + zhibridge +
  atomcode），召集 recipients 用 "all"（总线侧广播）。

铁律锚点：
- 铁律 7：缝 key proj/family-meeting（proj 域前缀）；
- J4：convene/poll/converge/assign 每步落 meeting record，失败如实入账不假活；
- L2：lingmessage MCP 不可达时召集失败如实返回（缺席降级，不本地假开会）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from lingclaude.plugins.agents.agent_family import McpAgentPluginBase

# 会议账本根（J4/N1：账目可对）
MEETINGS_ROOT = Path(__file__).resolve().parents[3] / "data" / "family_meetings"

# lingbus MCP 传输的 manifest（单一事实源：transport 配置在 agent_lingmessage）
_LINGMESSAGE_MANIFEST = Path(__file__).resolve().parents[1] / "agent_lingmessage" / "manifest.agent.json"

# 会议状态机合法变迁（超出即 record 语义破坏）
STATES = ("convened", "collecting", "converged", "assigned")


class FamilyMeetingPlugin:
    """家族会议编排（经 lingmessage lingbus MCP 传输，本类只做状态机+账本）。"""

    def __init__(self) -> None:
        self._bus = McpAgentPluginBase(_LINGMESSAGE_MANIFEST)

    def _call_bus(self, tool: str, arguments: dict) -> dict:
        """直调 lingbus 工具（不经基类 run：基类注入 caller 键，lingbus 工具无此参，
        pydantic 拒收——lingbus 用 caller_signature 鉴权，不收 caller）。"""
        import subprocess
        manifest = self._bus._manifest
        t = manifest["transport"]
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "family-meeting", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool, "arguments": arguments}}),
        ]
        proc = subprocess.Popen(t["command"], cwd=t.get("cwd"),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        try:
            # stdin 必须保持打开到读到响应（communicate 一次性关闭会让 open_thread
            # 这类慢工具输掉了与 server 退出的竞态——tools/list 快能回，open_thread 挂）
            proc.stdin.write("\n".join(lines) + "\n")
            proc.stdin.flush()
            import time as _t
            deadline = _t.monotonic() + t.get("call_timeout_s", 120)
            resp = None
            while _t.monotonic() < deadline:
                ln = proc.stdout.readline()
                if not ln:
                    break
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if d.get("id") == 1:
                    resp = d
                    break
            if resp is None or "error" in resp or "result" not in resp:
                raise RuntimeError(f"lingbus {tool} failed: {str((resp or {}).get('error', 'no response'))[:200]}")
            content = resp["result"].get("content", [])
            text = content[0].get("text", "") if content else ""
            return json.loads(text) if text.strip().startswith(("{", "[")) else {"raw": text}
        finally:
            if proc.poll() is None:
                proc.terminate()

    @property
    def name(self) -> str:
        return "proj/family-meeting"

    # ── 账本 ──────────────────────────────────────────────────────────
    def _meeting_path(self, meeting_id: str) -> Path:
        return MEETINGS_ROOT / f"{meeting_id}.json"

    def _load(self, meeting_id: str) -> dict:
        p = self._meeting_path(meeting_id)
        if not p.is_file():
            raise ValueError(f"unknown meeting: {meeting_id!r}")
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, meeting: dict) -> None:
        MEETINGS_ROOT.mkdir(parents=True, exist_ok=True)
        self._meeting_path(meeting["meeting_id"]).write_text(
            json.dumps(meeting, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 工具 1：召集 ──────────────────────────────────────────────────
    def convene(self, topic: str, agenda: list[str], when: str = "",
                moderator: str = "lingclaude", channel: str = "ecosystem") -> dict:
        """召集会议：LingBus open_thread 广播议题+议程+时间，会议 record 落账。

        topic: 议题标题；agenda: 讨论点 1.2.3...；when: 开会时间（如 "20:00"）；
        moderator: 主持人身份（默认 lingclaude，收敛时以此身份发结论）。
        """
        agenda_lines = "\n".join(f"{i+1}. {a}" for i, a in enumerate(agenda))
        body = (
            f"【会议召集】议题：{topic}\n"
            f"时间：{when or '尽快'}（收到请在本线程回复确认）\n"
            f"主持：{moderator}\n议程：\n{agenda_lines}\n"
            f"流程：确认出席 → 各子发言（@{moderator}）→ 主持收敛 → 派发执行"
        )
        r = self._call_bus("open_thread", {
            "topic": f"会议：{topic}", "sender": moderator,
            "recipients": "all", "channel": channel, "subject": f"会议召集：{topic}",
            "body": body,
        })
        if "error" in r:
            return {"state": "failed", "error": r["error"],
                    "note": "L2：总线拒收，召集未生效不假活"}
        meeting_id = f"mtg_{time.strftime('%Y%m%d_%H%M%S')}"
        meeting = {
            "meeting_id": meeting_id, "thread_id": r["thread_id"],
            "topic": topic, "agenda": agenda, "when": when,
            "moderator": moderator, "channel": channel,
            "state": "convened", "created_at": time.time(),
            "attendees": [], "speeches": [], "resolution": "", "assignments": [],
        }
        self._save(meeting)
        return {"state": "succeeded", "meeting_id": meeting_id,
                "thread_id": r["thread_id"], "meeting_state": "convened"}

    # ── 工具 2：收集发言（含出席确认）────────────────────────────────
    def collect(self, meeting_id: str, wait_s: int = 0) -> dict:
        """收集与会发言：poll 主持人消息，把本线程新消息记入 speeches/attendees。"""
        mtg = self._load(meeting_id)
        since = int(mtg.get("last_rowid", 0))
        msgs = self._call_bus("poll_messages", {
            "recipient": mtg["moderator"], "since_rowid": since, "limit": 200})
        # poll_messages 返回 list[dict]（区别于 open/post 的 dict）；raw=非 JSON 文本回包
        if isinstance(msgs, dict):
            return {"state": "failed", "error": f"poll failed: {str(msgs.get('raw', msgs))[:200]}"}
        new = [m for m in msgs if m.get("thread_id") == mtg["thread_id"]]
        max_row = since
        for m in new:
            row = int(m.get("rowid", 0))
            max_row = max(max_row, row)
            sender = m.get("sender", "?")
            body = m.get("body", "")
            if mtg["state"] == "convened" and ("确认" in body or "出席" in body):
                if sender not in mtg["attendees"]:
                    mtg["attendees"].append(sender)
            else:
                mtg["speeches"].append({"sender": sender, "body": body[:2000],
                                        "rowid": row, "at": time.time()})
        if new and mtg["state"] == "convened":
            mtg["state"] = "collecting"
        mtg["last_rowid"] = max_row
        self._save(mtg)
        return {"state": "succeeded", "meeting_state": mtg["state"],
                "attendees": mtg["attendees"], "new_speeches": len(new),
                "total_speeches": len(mtg["speeches"])}

    # ── 工具 3：主持收敛 ──────────────────────────────────────────────
    def converge(self, meeting_id: str, resolution: str) -> dict:
        """主持收敛：灵X（moderator）把结论发回线程，会议转 converged。"""
        mtg = self._load(meeting_id)
        if mtg["state"] not in ("collecting", "convened"):
            return {"state": "failed", "error": f"cannot converge from {mtg['state']}"}
        r = self._call_bus("post_reply", {
            "thread_id": mtg["thread_id"], "sender": mtg["moderator"],
            "recipient": "all", "subject": f"收敛：{mtg['topic']}",
            "body": f"【会议收敛】{resolution}"})
        if "error" in r:
            return {"state": "failed", "error": r["error"]}
        mtg["resolution"] = resolution
        mtg["state"] = "converged"
        self._save(mtg)
        return {"state": "succeeded", "meeting_state": "converged",
                "speeches_count": len(mtg["speeches"])}

    # ── 工具 4：派发执行 ──────────────────────────────────────────────
    def assign(self, meeting_id: str, assignments: list[dict]) -> dict:
        """派发执行：按 [{member, task}] 逐个 @成员 派任务，会议转 assigned。

        监督语义：派发即建 meeting 内 assignments 账（member/task/dispatched_at），
        执行回执由 collect 持续收（member 回复线程即销项依据）。
        """
        mtg = self._load(meeting_id)
        if mtg["state"] != "converged":
            return {"state": "failed", "error": f"cannot assign from {mtg['state']}"}
        dispatched = []
        for a in assignments:
            member, task = a.get("member", ""), a.get("task", "")
            r = self._call_bus("post_reply", {
                "thread_id": mtg["thread_id"], "sender": mtg["moderator"],
                "recipient": member, "subject": f"执行：{mtg['topic']}",
                "body": f"【任务派发】{task}\n（来源：会议 {mtg['meeting_id']} 收敛决议，完成后本线程回执）"})
            ok = "error" not in r
            item = {"member": member, "task": task, "dispatched": ok,
                    "dispatched_at": time.time()}
            mtg["assignments"].append(item)
            dispatched.append(item)
        mtg["state"] = "assigned"
        self._save(mtg)
        return {"state": "succeeded", "meeting_state": "assigned",
                "dispatched": dispatched}

    # ── 工具 5：闭幕前唤醒缺席者（2026-09-19 实测经验固化）──────────────
    # 实测锚点：crush run + 各子目录执行（WAKE_UP.md 协议或身份指令）11/11 成功；
    # 总线 30s 间隔节流 → 每次唤醒后 sleep 31s；唤醒指令固定三段式（poll→回执→按议程发言）。

    # 12 子唤醒目录（身份→工程目录，crush run 的 cwd；org_member 账本为准）
    WAKE_DIRS = {
        "lingtong": "lingflow", "lingke": "lingclaude", "lingyan": "lingresearch",
        "lingzhi": "lingzhi", "lingtongask": "lingtongask", "zhibridge": "zhibridge",
        "lingxi": "lingxi", "lingxin": "lingmessage", "lingwang": "lingweb",
        "lingjiyou": "lingminopt", "lingyang": "lingyang", "lingchuang": "lingcreate",
        "lingan": "lingan",
    }
    # lingbus 身份名 → 唤醒目录（poll_messages 的 recipient 口径）
    WAKE_DIRS_BY_BUS = {
        "lingtong": "lingflow", "lingclaude": "lingclaude", "lingresearch": "lingresearch",
        "lingzhi": "lingzhi", "lingtongask": "lingtongask", "zhibridge": "zhibridge",
        "lingxi": "lingxi", "lingmessage": "lingmessage", "lingweb": "lingweb",
        "lingminopt": "lingminopt", "lingyang": "lingyang", "lingcreate": "lingcreate",
        "lingan": "lingan",
    }

    def wake_absentees(self, meeting_id: str, absentees: list[str],
                       agenda_hint: str = "") -> dict:
        """闭幕前唤醒缺席者：逐子 crush run（WAKE_UP.md 协议），唤醒后验证回执。

        固化 2026-09-19 实测经验：crush run 在各子目录跑，指令三段式——
        poll_messages(recipient='<bus_id>') → 本线程回执出席 → 按 agenda_hint 发言。
        总线 30s 节流：每次唤醒间隔 31s；单子唤醒结果如实入账（J4，失败不假活）。
        返回 {awake:[...], failed:[...], receipts:{member: message_id}}。
        """
        mtg = self._load(meeting_id)
        tid = mtg["thread_id"]
        import subprocess, time as _t
        results = {"awake": [], "failed": [], "receipts": {}, "details": []}
        for i, bus_id in enumerate(absentees):
            d = self.WAKE_DIRS_BY_BUS.get(bus_id)
            if not d or not Path(f"/home/ai/{d}").is_dir():
                results["failed"].append({"member": bus_id, "reason": "no dir"})
                continue
            prompt = (
                f"读取 WAKE_UP.md 执行唤醒协议（无则按 AGENTS.md 身份锚定）："
                f"LingBus 会议线程 {tid} 正在进行（{mtg['topic']}）。"
                f"请 poll_messages(recipient='{bus_id}') 后在该线程回复确认出席，"
                f"并{agenda_hint or '按会议议程发言'}。")
            try:
                r = subprocess.run(
                    ["crush", "run", prompt], cwd=f"/home/ai/{d}",
                    capture_output=True, text=True, timeout=150)
                ok = r.returncode == 0 and len(r.stdout.strip()) > 20
            except (subprocess.TimeoutExpired, OSError) as e:
                ok = False
                r = type("R", (), {"returncode": -1, "stdout": "", "stderr": str(e)[:200]})()
            # 唤醒后查线程拿回执 message_id（30s 节流窗口后 poll）
            _t.sleep(31)
            msgs = self._call_bus("poll_messages", {
                "recipient": mtg["moderator"], "since_rowid": int(mtg.get("last_rowid", 0)),
                "limit": 200})
            receipt_id = None
            if isinstance(msgs, list):
                for x in msgs:
                    if x.get("sender") == bus_id and x.get("thread_id") == tid:
                        receipt_id = x.get("message_id")
                        new_row = int(x.get("rowid", 0))
                        if new_row > int(mtg.get("last_rowid", 0)):
                            mtg["last_rowid"] = new_row
                        break
            item = {"member": bus_id, "woken": ok, "receipt": receipt_id,
                    "exit": getattr(r, "returncode", -1)}
            mtg["attendees"] = sorted(set(mtg.get("attendees", [])) |
                                      ({bus_id} if receipt_id else set()))
            results["details"].append(item)
            if receipt_id:
                results["awake"].append(bus_id)
                results["receipts"][bus_id] = receipt_id
            else:
                results["failed"].append({"member": bus_id,
                                          "reason": f"no receipt (exit={item['exit']})"})
            self._save(mtg)
        return results

"""Session persistence helpers extracted from QueryEngine."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lingclaude.core.context_engine import SUMMARY_HEADLINE
from lingclaude.core.models import UsageSummary
from lingclaude.core.redact import redact as _redact_text
from lingclaude.core.session import Session, _project_dir_name
from lingclaude.core.types import Result

logger = logging.getLogger(__name__)


class SessionPersister:
    """Encapsulates session persistence logic extracted from QueryEngine."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def persist_session(self) -> Result[str]:
        engine = self._engine
        # 空会话防误写：load_session 后未对话即退出（或 /clear 后退出）时，
        # 空消息列表会把既有存档覆盖成空壳（2026-09-06 7c643abc 0 字节事故）。
        if not engine._messages:
            return Result.fail("当前会话无消息，跳过保存（保护既有存档）", code="EMPTY_SESSION")
        # 多会话写互斥（2026-10-01 线程写锁，源自 codex thread-writer-locks 设计）：
        # --continue/--resume 双开会话解析到同一 session_id，各自退出时先后
        # save() 同一 json——原子写只保证"不写坏"，防不了后写者整体覆盖前写者
        # （2026-09-21 research 文档记录的 lost-update 事故模式）。此锁 + mtime
        # 检测把顺序踩踏纪律代码化。
        from lingclaude.core.file_lock import file_edit_lock

        target = self._session_target_path()
        lock_cm = file_edit_lock(target, owner="session_persist", timeout=10.0)
        try:
            lock_cm.__enter__()
        except (TimeoutError, OSError) as e:
            # fail-open（区别于编辑锁的 fail-closed）：保存被卡死比覆盖风险更伤
            # ——会话随时可能被用户关掉。降级为无锁保存，台账留痕。
            logger.warning("session 写锁获取失败（%s），降级无锁保存: session=%s", e, engine.session_id)
            return self._persist_locked(conflict=False)
        try:
            return self._persist_locked(conflict=self._mtime_conflict(target))
        finally:
            lock_cm.__exit__(None, None, None)
        # 注：_persist_locked 内部在 conflict=True 时**先 fork 后落 fork 档**，
        # 主档全程不写——「原档未被覆盖」是本机制的硬契约，由测试锚定。

    def _session_target_path(self) -> str:
        """当前会话的主存档路径（锁的粒度对象）。"""
        engine = self._engine
        return str(
            engine.session_manager.save_dir
            / _project_dir_name(os.getcwd())
            / f"{engine.session_id}.json"
        )

    def _mtime_conflict(self, target: str) -> bool:
        """加载基线之后的存档是否被别人改过（顺序踩踏检测）。

        基线 = load_session 时刻的 mtime（未加载过则记本次写入前 mtime）。
        """
        engine = self._engine
        baseline = getattr(engine, "_session_mtime_baseline", None)
        p = Path(target)
        if baseline is None or not p.exists():
            return False
        try:
            return p.stat().st_mtime > baseline + 1e-6
        except OSError:
            return False

    def _refresh_baseline(self, written_path: str) -> None:
        """保存成功后刷新 mtime 基线。

        2026-10-02 fork 连环套娃修复：基线只赋值不刷新时，同会话第二次
        persist 起必把**自己**上次写入误判为「他人修改」（写入后 mtime 恒
        > 旧基线），每次保存 fork 一层（e1ef82cf… 6 层叉链实锤）。以
        session_manager.save 返回的实际落盘路径为准 stat，兼容 fork 切 id
        与 save_dir 重定向；stat 失败置 None（fail-open，等同无基线语义）。
        """
        engine = self._engine
        try:
            engine._session_mtime_baseline = Path(written_path).stat().st_mtime
        except OSError:
            engine._session_mtime_baseline = None

    def _persist_locked(self, conflict: bool) -> Result[str]:
        """持锁后的真实保存体。conflict=True 时只落 fork 档，主档一字不动。"""
        engine = self._engine
        session = Session(
            session_id=engine.session_id,
            messages=tuple(engine._messages),
            input_tokens=engine._usage.input_tokens,
            output_tokens=engine._usage.output_tokens,
            # 2026-09-24 cache 口径修复: 累计 cached 一并落盘（旧版丢弃 →
            # -continue 后 cache% 虚低，见 Session.cached_tokens 注释）
            cached_tokens=engine._usage.cached_tokens,
            # 2026-09-15（会话问题重构 P1-2）: 保存带 project_path —— 此前
            # Session 的 project_path 字段存在但 persist 从不传，导致所有会话
            # 落 _default/ 目录，跨项目混在一起（-continue 取全局最近）。现
            # 以 os.getcwd() 为项目归属，list_sessions(project_path) 即可按
            # 当前目录过滤，杜绝跨项目会话泄露。
            project_path=os.getcwd(),
        )
        # 冲突路径：主档已被他人改过 → 本会话内容一律落 fork 档，主档不写。
        if conflict:
            return self._save_fork(engine, session)
        result = engine.session_manager.save(session)
        if result.is_error:
            return result  # type: ignore[return-value]
        # 2026-10-07 有界存储：存档落盘后按数量留尾（--continue 只消费最近存档）
        from lingclaude.core.retention import prune_sessions_on_persist
        prune_sessions_on_persist(engine.session_manager.save_dir)
        self._refresh_baseline(str(result.data))
        return Result.ok(str(result.data))

    def _save_fork(self, engine: Any, session: Session) -> Result[str]:
        """把本会话内容 fork 成独立 id 落盘，内存 session_id 切到 fork。"""
        import secrets

        fork_id = f"{session.session_id}-fork{secrets.token_hex(3)}"
        fork_session = Session(
            session_id=fork_id,
            messages=session.messages,
            input_tokens=session.input_tokens,
            output_tokens=session.output_tokens,
            cached_tokens=session.cached_tokens,
            project_path=session.project_path,
            project_name=session.project_name,
        )
        fork_result = engine.session_manager.save(fork_session)
        if fork_result.is_error:
            logger.warning(
                "会话冲突 fork 保存失败（原档未被覆盖）: %s", fork_result.error
            )
            return Result.fail(
                f"检测到并发修改，fork 保存失败: {fork_result.error}",
                code="CONFLICT_FORK_SAVE_ERROR",
            )
        engine.session_id = fork_id
        self._refresh_baseline(str(fork_result.data))
        print(
            f"[session] ⚠ 检测到存档被其他会话修改，本会话已另存为 {fork_id}"
            f"（原档 {session.session_id} 未被覆盖）"
        )
        return Result.ok(str(fork_result.data))

    def load_session(self, session_id: str) -> bool:
        engine = self._engine
        result = engine.session_manager.load(session_id)
        if result.is_error:
            return False
        session = result.data
        engine.session_id = session.session_id
        # 写锁配套（2026-10-01）：记录加载基线 mtime，persist 时对比——
        # 若存档在此之后被其他会话写过（> 基线），判定顺序踩踏，fork 保存。
        try:
            engine._session_mtime_baseline = (
                engine.session_manager.save_dir
                / _project_dir_name(os.getcwd())
                / f"{session_id}.json"
            ).stat().st_mtime
        except OSError:
            engine._session_mtime_baseline = None
        engine._messages = list(session.messages)
        # 2026-09-24 cache 口径修复: cached 随会话恢复（旧版位置参数重建丢弃
        # cached → 归零；分母 input 却恢复全历史 → cache% = 新cached/全历史input 虚低）
        engine._usage = UsageSummary(
            session.input_tokens, session.output_tokens, session.cached_tokens
        )
        engine._transcript = list(session.messages)
        engine._conversation.clear()
        # 2026-09-29 (会话恢复角色错位修复):
        # - 压缩摘要落盘为首条（system 语义），单独处理；
        # - 剩余严格成对；奇数尾条按 assistant 补上（压缩后 assistant 收尾是常态）。
        msgs = session.messages
        pos = 0
        # 首条：摘要 → system
        if msgs and _is_summary(msgs[0]):
            engine._conversation.append(("system", _redact_text(msgs[0])))
            pos = 1
        # 成对循环
        while pos < len(msgs):
            engine._conversation.append(("user", _redact_text(msgs[pos])))
            pos += 1
            if pos < len(msgs):
                engine._conversation.append(("assistant", _redact_text(msgs[pos])))
                pos += 1
            else:
                # 奇数尾条：assistant 收尾（压缩后常态），不丢弃
                engine._conversation.append(("assistant", ""))
                break
        return True

    def clear_checkpoint(self) -> None:
        engine = self._engine
        engine._sync_session_store()
        engine.session_store.clear_checkpoint()
        engine._active_checkpoint = None
        # C 路线二期（2026-09-23）：clear 事件入流——rebuild 侧以此作废
        # 此前内嵌快照，防止正常收尾会话被事件流误复活（best-effort）。
        try:
            from lingclaude.core.rollout import record_engine_rollout
            record_engine_rollout(engine, "checkpoint_clear", {})
        except Exception:  # noqa: BLE001
            logger.debug("rollout checkpoint_clear record failed", exc_info=True)

    def save_checkpoint(
        self,
        messages: list[Any],
        round_idx: int,
        prompt: str,
        used_tools: bool,
        total_input: int,
        total_output: int,
        tag: str | None = None,
        total_cached: int = 0,
    ) -> None:
        engine = self._engine
        engine._sync_session_store()
        cp = engine.session_store.save_checkpoint(
            messages=messages,
            round_idx=round_idx,
            prompt=prompt,
            used_tools=used_tools,
            total_input=total_input,
            total_output=total_output,
            total_cached=total_cached,
            conversation=engine._conversation,
            tag=tag,
        )
        if cp is not None:
            engine._active_checkpoint = cp
            # C 路线统一（2026-09-23）：快照动作同步记入共享 rollout 事件流
            # （单一真理之源一期）。best-effort：失败不影响 checkpoint 主路径。
            try:
                from lingclaude.core.rollout import record_engine_rollout
                record_engine_rollout(engine, "checkpoint", {
                    "source": "session_persist",
                    "tag": tag,
                    "round_idx": round_idx,
                    "used_tools": used_tools,
                    "message_count": len(messages),
                    "total_input": total_input,
                    "total_output": total_output,
                })
            except Exception:  # noqa: BLE001
                logger.debug("rollout checkpoint record failed", exc_info=True)

    def list_checkpoints(self) -> list[dict[str, Any]]:
        engine = self._engine
        engine._sync_session_store()
        return engine.session_store.list_checkpoints()

    def rewind_to(self, tag: str) -> bool:
        """回滚到指定 tag 的 checkpoint（P1 rewind）。

        恢复 engine._messages / _conversation / _transcript 到该版本，
        不动 session_store 里其它版本（可再前进）。
        """
        engine = self._engine
        engine._sync_session_store()
        cd = engine.session_store.load_checkpoint_by_tag(tag)
        if cd is None:
            return False
        from lingclaude.core.model_types import ModelMessage, MessageRole, ToolCall

        messages: list[ModelMessage] = []
        for rm in cd.raw_messages:
            role = MessageRole(rm.get("role", "user"))
            tool_calls = None
            if rm.get("tool_calls"):
                tool_calls = tuple(
                    ToolCall(
                        id=tc["function"].get("id", ""),
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in rm["tool_calls"]
                    if "function" in tc
                )
            messages.append(ModelMessage(
                role=role,
                content=rm.get("content", ""),
                name=rm.get("name"),
                tool_call_id=rm.get("tool_call_id"),
                tool_calls=tool_calls,
            ))
        engine._messages = messages
        if cd.saved_conversation:
            engine._conversation = [
                (tuple(item) if isinstance(item, (list, tuple)) and len(item) == 2 else item)
                for item in cd.saved_conversation
            ]
        engine._transcript = [m.content for m in messages if isinstance(m.content, str)]
        # 回滚后 journal 清空（回滚点之后的事件全部作废，防副作用幂等误判）
        try:
            engine._get_journal().clear()
            engine._journal_cache = None
        except Exception:
            pass
        logger.info("Session rewound to tag=%s (round=%d, msgs=%d)", tag, cd.round_idx, len(messages))
        # C 路线统一（2026-09-23）：rewind 也是快照动作，同一事件流记录
        # （best-effort）——rollout replay 时可见回滚点。
        try:
            from lingclaude.core.rollout import record_engine_rollout
            record_engine_rollout(engine, "rewind", {
                "source": "session_persist",
                "tag": tag,
                "round_idx": cd.round_idx,
                "message_count": len(messages),
            })
        except Exception:  # noqa: BLE001
            logger.debug("rollout rewind record failed", exc_info=True)
        return True

    def load_checkpoint(self) -> dict[str, Any] | None:
        engine = self._engine
        engine._sync_session_store()
        cp_path = Path(engine.session_store._checkpoint_dir) / f"{engine.session_id}.json"
        cd = engine.session_store.load_checkpoint()
        if cd is None:
            # C 路线二期（2026-09-23）：JSON 缓存缺失/损坏 → 从 rollout 事件流
            # 重建（派生 fallback）。事件流 append-only，崩溃时通常是完整的——
            # 缓存可丢，真理不丢。
            cd = engine.session_store.rebuild_checkpoint_from_events()
            if cd is None:
                return None
            logger.warning(
                "Checkpoint JSON 不可用，已从 rollout 事件流重建（session=%s round=%s）",
                engine.session_id, cd.round_idx,
            )
        engine._active_checkpoint = cp_path
        return {
            "session_id": cd.session_id,
            "prompt": cd.prompt,
            "round_idx": cd.round_idx,
            "used_tools": cd.used_tools,
            "total_input": cd.total_input,
            "total_output": cd.total_output,
            "messages": cd.raw_messages,
            "conversation": cd.saved_conversation,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_summary(msg: str) -> bool:
    """判断消息是否为压缩摘要（与 context_engine.is_summary_entry 同一锚点）。"""
    return msg.lstrip().startswith(SUMMARY_HEADLINE)

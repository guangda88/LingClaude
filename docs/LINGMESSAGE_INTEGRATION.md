# lingmessage Integration (灵信)

> 从 AGENTS.md 迁移（2026-05-06 瘦身）。原始备份：`docs/AGENTS_ARCHIVE_20260506.md`

```python
from lingmessage.mailbox import Mailbox
from lingmessage.types import LingIdentity, Channel, MessageType

# Mount mailbox into query engine
mailbox = Mailbox()  # defaults to ~/.lingmessage/
engine.init_mailbox(mailbox)

# Read threads
threads = engine.read_lingmessage_threads()

# Post a finding to lingmessage
mailbox.reply(
    thread_id=some_thread_id,
    sender=LingIdentity.LINGCLAUDE,
    recipient=LingIdentity.ALL,
    subject="灵克发现：JSON 处理模式需要优化",
    body="...",
)
```

- `init_mailbox(mailbox)` — receives a `lingmessage.Mailbox` instance (optional, zero-impact if not called)
- `read_lingmessage_threads()` — returns `tuple[ThreadHeader, ...]` (empty tuple if no mailbox)
- lingmessage is a **separate project** at `/home/ai/lingmessage/` with its own repo
- Zero-dependency integration: lingmessage is imported at call site, not at module level

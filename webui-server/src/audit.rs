//! AuditLogger — JSONL 审计日志。
//!
//! P4.2 已接线：`auth_middleware` 对每请求调 `log_request`（含 401 拒绝），
//! `chat_sse` 引擎错误处调 `log_event`。落盘路径:
//! `LINGCLAUDE_WEBUI_AUDIT_LOG` 或默认 `.lingclaude/webui-audit.jsonl`。
//!
//! P3 清偿（2026-09-26 审计项「audit 日志静默丢失」）：
//! - 打开失败不再伪装成 /dev/null 正常日志 —— `opened=false` 显式降级，
//!   状态经 `/health`（无鉴权）与 `/status` 暴露，供就绪探测告警。
//! - 写入失败不再 `let _ =` 吞掉 —— `write_errors` 计数器递增。
//!   审计故障不 panic（不打挂业务请求路径），但必须可观测。

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::ops::DerefMut;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// 审计健康快照（/status 与 /health 共用）。
#[derive(Clone, Copy, Debug)]
pub(crate) struct AuditStats {
    pub(crate) opened: bool,
    pub(crate) write_errors: u64,
    pub(crate) requests_logged: u64,
    pub(crate) events_logged: u64,
}

#[derive(Clone)]
pub(crate) struct AuditLogger {
    /// 打开失败时为 None → 进入显式降级（条目丢弃 + 计数）。
    handle: Arc<Option<Mutex<File>>>,
    opened: Arc<AtomicBool>,
    write_errors: Arc<AtomicU64>,
    requests_logged: Arc<AtomicU64>,
    events_logged: Arc<AtomicU64>,
}

impl AuditLogger {
    pub(crate) fn new(log_path: PathBuf) -> Self {
        let (handle, opened) = match OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
        {
            Ok(f) => (Some(Mutex::new(f)), true),
            Err(e) => {
                eprintln!(
                    "WARN: 审计日志不可用 {}: {} — 审计条目将被丢弃（见 /health audit_opened=false）",
                    log_path.display(),
                    e
                );
                (None, false)
            }
        };
        AuditLogger {
            handle: Arc::new(handle),
            opened: Arc::new(AtomicBool::new(opened)),
            write_errors: Arc::new(AtomicU64::new(0)),
            requests_logged: Arc::new(AtomicU64::new(0)),
            events_logged: Arc::new(AtomicU64::new(0)),
        }
    }

    /// 审计健康快照。
    pub(crate) fn stats(&self) -> AuditStats {
        AuditStats {
            opened: self.opened.load(Ordering::Relaxed),
            write_errors: self.write_errors.load(Ordering::Relaxed),
            requests_logged: self.requests_logged.load(Ordering::Relaxed),
            events_logged: self.events_logged.load(Ordering::Relaxed),
        }
    }

    /// 写入一条审计 JSONL（唯一收口：未打开=丢弃+计数；写失败=计数，不 panic）。
    fn write_entry(&self, entry: serde_json::Value, is_request: bool) {
        let Some(guard) = self.handle.as_ref() else {
            // 显式降级：条目丢弃仍计数 —— write_errors>0 即审计有缺口。
            self.write_errors.fetch_add(1, Ordering::Relaxed);
            return;
        };
        let mut f = guard.lock().unwrap();
        match writeln!(f.deref_mut(), "{}", entry).and_then(|_| f.flush()) {
            Ok(()) => {
                if is_request {
                    self.requests_logged.fetch_add(1, Ordering::Relaxed);
                } else {
                    self.events_logged.fetch_add(1, Ordering::Relaxed);
                }
            }
            Err(_) => {
                // 高频路径（每请求）只计数不刷 stderr；/health 可观测。
                self.write_errors.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// 记录一次 HTTP 请求审计条目。
    pub(crate) fn log_request(
        &self,
        method: &str,
        path: &str,
        session_id: Option<&str>,
        status: u16,
        error: Option<&str>,
    ) {
        let entry = serde_json::json!({
            "type": "request",
            "ts": chrono::Utc::now().timestamp_millis(),
            "method": method,
            "path": path,
            "session_id": session_id.unwrap_or(""),
            "status": status,
            "error": error,
        });
        self.write_entry(entry, true);
    }

    /// 记录 SSE 事件（仅 error/done 类型，避免高频事件日志膨胀）。
    pub(crate) fn log_event(&self, event_type: &str, data: &str, session_id: Option<&str>) {
        if !matches!(event_type, "error" | "done" | "approval") {
            return;
        }
        let entry = serde_json::json!({
            "type": "sse_event",
            "ts": chrono::Utc::now().timestamp_millis(),
            "event": event_type,
            "session_id": session_id.unwrap_or(""),
            "data_preview": data.chars().take(200).collect::<String>(),
        });
        self.write_entry(entry, false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_failure_degrades_visibly_not_silently() {
        // 父目录不存在 ⇒ 打开必失败 ⇒ opened=false 且条目丢弃计数。
        let logger = AuditLogger::new(PathBuf::from(
            "/nonexistent-lingclaude-audit-selftest/dir/x.jsonl",
        ));
        let s = logger.stats();
        assert!(!s.opened, "open 失败必须显式暴露 opened=false");
        logger.log_request("GET", "/status", None, 200, None);
        let s = logger.stats();
        assert!(
            s.write_errors >= 1,
            "降级期写入必须计入 write_errors（不可静默）"
        );
        assert_eq!(s.requests_logged, 0, "降级期不应虚报成功条数");
    }

    #[test]
    fn successful_writes_counted_and_filtered() {
        let path = std::env::temp_dir().join("lingclaude-audit-selftest-ok.jsonl");
        let _ = std::fs::remove_file(&path);
        let logger = AuditLogger::new(path);
        assert!(logger.stats().opened);
        logger.log_request("GET", "/chat", None, 401, None);
        logger.log_event("error", "engine down", None);
        logger.log_event("message", "被过滤的高频事件不应入账", None);
        let s = logger.stats();
        assert_eq!(s.requests_logged, 1);
        assert_eq!(s.events_logged, 1, "非 error/done/approval 事件必须被过滤");
        assert_eq!(s.write_errors, 0);
    }
}

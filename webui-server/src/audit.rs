//! Harness 审计日志 — JSONL 格式，对齐 DSH datalog。
//!
//! P4.2 已接线：`auth_middleware` 对每请求调 `log_request`（含 401 拒绝），
//! `chat_sse` 引擎错误处调 `log_event`。落盘路径:
//! `LINGCLAUDE_WEBUI_AUDIT_LOG` 或默认 `.lingclaude/webui-audit.jsonl`。

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[derive(Clone)]
pub(crate) struct AuditLogger {
    log_path: PathBuf,
    handle: Arc<Mutex<File>>,
}

impl AuditLogger {
    pub(crate) fn new(log_path: PathBuf) -> Self {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .unwrap_or_else(|e| {
                eprintln!("WARN: 无法打开审计日志 {}: {}", log_path.display(), e);
                File::create("/dev/null").unwrap()
            });
        AuditLogger {
            log_path,
            handle: Arc::new(Mutex::new(file)),
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
        let mut f = self.handle.lock().unwrap();
        let _ = writeln!(f, "{}", entry);
        let _ = f.flush();
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
        let mut f = self.handle.lock().unwrap();
        let _ = writeln!(f, "{}", entry);
        let _ = f.flush();
    }
}

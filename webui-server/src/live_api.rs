//! `/live` 实时状态同步 SSE — HTTP 桥接 lingclaude 引擎。

use axum::{
    extract::{Query, State},
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
};
use serde::Deserialize;
use std::time::Duration;

use crate::AppState;

/// lingclaude 8700 `/status` 响应（契约见 lingclaude/api.py）。
#[derive(Deserialize)]
struct LingclaudeStatus {
    status: String,
    version: Option<String>,
    #[serde(default)]
    projects: Vec<String>,
    #[serde(default)]
    auth_required: bool,
}

/// 桥接层 SSE 错误事件 — 必经 serde 构造合法 JSON（契约同 chat_api）。
fn error_event(message: String) -> Event {
    Event::default()
        .event("error")
        .json_data(serde_json::json!({"type": "error", "message": message}))
        .expect("json! 序列化不可能失败")
}

/// `/live` 实时状态同步 SSE — HTTP 桥接 lingclaude 引擎（8700 `/status` + `/live/events`）。
///
/// 前端 LiveWireEvent 事件形状（api.ts L696-717）：
/// `snapshot {messages, session_id, project_hash, provider, mode}` + 增量事件。
/// 实现：
/// 1. 首次推 `snapshot`（桥接 8700 /status，含引擎在线状态；回显 `session_id` 查询参数）
/// 2. 周期轮询 8700 `/live/events?since=N`，有新事件则转发 `tool_start` / `tool_result`
/// 3. 本 tick 无新事件才推心跳保活（对齐 webui-v0.1.md §4.2）；桥接失败推 `error`
pub(crate) async fn live_sse(
    State(state): State<AppState>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Response {
    let base = state.lingclaude_base.clone();
    let api_key = state.lingclaude_api_key.clone();
    let client = state.client.clone();
    let session_id = q.get("session_id").cloned().unwrap_or_default();

    let stream = async_stream::stream! {
        // 1. 首次 snapshot（桥接 /status）
        {
            let mut builder = client.get(format!("{base}/status"));
            if let Some(key) = &api_key {
                builder = builder.header("X-API-Key", key);
            }
            let status_json = match builder.send().await {
                Ok(resp) if resp.status().is_success() => match resp.json::<LingclaudeStatus>().await {
                    Ok(s) => serde_json::json!({
                        "type": "snapshot",
                        "messages": [],
                        "session_id": session_id,
                        "project_hash": "",
                        "provider": "lingclaude",
                        "mode": "build",
                        "engine": { "status": s.status, "version": s.version, "projects": s.projects, "auth_required": s.auth_required }
                    }),
                    Err(e) => serde_json::json!({"type": "error", "message": format!("bridge parse failed: {e}")}),
                },
                Ok(resp) => serde_json::json!({"type": "error", "message": format!("engine HTTP {}", resp.status())}),
                Err(e) => serde_json::json!({"type": "error", "message": format!("engine unreachable: {e}")}),
            };
            // snapshot 可能本身是 error 形状 — 事件名跟随，前端按 type 分派
            let ev_type = if status_json["type"] == "error" { "error" } else { "snapshot" };
            yield Ok::<Event, std::convert::Infallible>(
                Event::default().event(ev_type).json_data(&status_json).expect("Value 序列化不可能失败"),
            );
        }

        // 2-3. 周期轮询 /live/events 增量转发 + 心跳（无新事件时）
        let mut since: i64 = 0;
        loop {
            tokio::time::sleep(Duration::from_secs(3)).await;
            let mut forwarded = 0usize;
            let mut builder = client.get(format!("{base}/live/events?since={since}"));
            if let Some(key) = &api_key {
                builder = builder.header("X-API-Key", key);
            }
            match builder.send().await {
                Ok(resp) if resp.status().is_success() => {
                    #[derive(Deserialize)]
                    struct LiveEventsResp {
                        events: Vec<serde_json::Value>,
                        latest: i64,
                    }
                    match resp.json::<LiveEventsResp>().await {
                        Ok(r) => {
                            since = r.latest;
                            for ev in r.events {
                                forwarded += 1;
                                let ev_type = ev.get("type").and_then(|t| t.as_str()).unwrap_or("state").to_string();
                                yield Ok::<Event, std::convert::Infallible>(
                                    Event::default().event(ev_type).json_data(&ev).expect("Value 序列化不可能失败"),
                                );
                            }
                        }
                        Err(e) => {
                            yield Ok(error_event(format!("live events parse failed: {e}")));
                        }
                    }
                }
                Ok(resp) => {
                    yield Ok(error_event(format!("engine HTTP {}", resp.status())));
                }
                Err(e) => {
                    yield Ok(error_event(format!("engine unreachable: {e}")));
                }
            }
            if forwarded == 0 {
                // 心跳保活 — 仅本 tick 无新事件时（对齐 §4.2）
                let ts = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                yield Ok::<Event, std::convert::Infallible>(
                    Event::default().event("heartbeat").json_data(serde_json::json!({ "ts": ts }))
                        .expect("json! 序列化不可能失败"),
                );
            }
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))).into_response()
}

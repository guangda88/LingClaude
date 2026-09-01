//! `/live` 实时状态同步 SSE — HTTP 桥接 lingclaude 引擎。

use axum::{
    extract::{Query, State},
    response::{sse::{Event, KeepAlive, Sse}, IntoResponse, Response},
};
use serde::Deserialize;
use std::collections::HashMap;
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

/// `/live` 实时状态同步 SSE — HTTP 桥接 lingclaude 引擎（8700 `/status` + `/live/events`）。
///
/// 前端 LiveWireEvent 事件形状（api.ts L696-717）：
/// `snapshot {messages, session_id, project_hash, provider, mode}` + 增量事件。
/// 实现：
/// 1. 首次推 `snapshot`（桥接 8700 /status，含引擎在线状态）
/// 2. 周期轮询 8700 `/live/events?since=N`，有新事件则转发 `tool_start` / `tool_result`
/// 3. 无新事件时推心跳保活；桥接失败推 `error`（fail-closed，不静默）
pub(crate) async fn live_sse(State(state): State<AppState>, Query(_q): Query<std::collections::HashMap<String, String>>) -> Response {
    let base = state.lingclaude_base.clone();
    let api_key = state.lingclaude_api_key.clone();
    let client = state.client.clone();

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
                        "session_id": "",
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
            yield Ok::<Event, std::convert::Infallible>(Event::default().event("snapshot").json_data(&status_json).unwrap());
        }

        // 2-3. 周期轮询 /live/events 增量转发 + 心跳
        let mut since: i64 = 0;
        loop {
            tokio::time::sleep(Duration::from_secs(3)).await;
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
                                let ev_type = ev.get("type").and_then(|t| t.as_str()).unwrap_or("state");
                                yield Ok::<Event, std::convert::Infallible>(
                                    Event::default().event(ev_type).json_data(&ev).unwrap(),
                                );
                            }
                        }
                        Err(e) => {
                            yield Ok::<Event, std::convert::Infallible>(
                                Event::default().event("error").data(format!("{{\"type\":\"error\",\"message\":\"live events parse failed: {e}\"}}")),
                            );
                        }
                    }
                }
                Ok(resp) => {
                    yield Ok::<Event, std::convert::Infallible>(
                        Event::default().event("error").data(format!("{{\"type\":\"error\",\"message\":\"engine HTTP {}\"}}", resp.status())),
                    );
                }
                Err(e) => {
                    yield Ok::<Event, std::convert::Infallible>(
                        Event::default().event("error").data(format!("{{\"type\":\"error\",\"message\":\"engine unreachable: {e}\"}}")),
                    );
                }
            }
            // 心跳保活
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs();
            yield Ok::<Event, std::convert::Infallible>(
                Event::default().event("heartbeat").data(format!("{{\"ts\":{ts}}}")),
            );
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))).into_response()
}

//! `/chat` SSE 流式对话 + `/chat/permission` 审批 + `/chat/stop` 停止。
//! 全部为对 lingclaude 引擎(8700 api.py)的 HTTP 桥接,失败一律 fail-closed。

use axum::{
    extract::State,
    http::StatusCode,
    response::{sse::{Event, KeepAlive, Sse}, IntoResponse, Response},
};
use serde::{Deserialize, Serialize};
use std::time::Duration;

use crate::AppState;

#[derive(Deserialize)]
struct ChatRequest {
    session_id: Option<String>,
    message: String,
    /// 接收契约字段：前端可传但桥接层当前未消费（对齐 AtomCode 请求形状）。
    #[serde(default)]
    #[allow(dead_code)]
    images: Vec<String>,
    #[serde(default)]
    #[allow(dead_code)]
    model: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    mode: String,
}

/// lingclaude 8700 api.py 的 `/ask` 请求体（契约见 lingclaude/api.py）。
#[derive(Serialize)]
struct LingclaudeAskRequest {
    question: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    context: Option<String>,
}

/// `/chat` SSE 流式对话 — HTTP 桥接 lingclaude 引擎（8700 `/ask/stream`）。
///
/// 桥接协议：
/// 1. 解析前端请求 → 构造 `{question, context}` POST 到 `{lingclaude_base}/ask/stream`
/// 2. 逐事件读取引擎 SSE 流，按事件边界（`\n\n`）切分，提取 `data:` 行原样转发
/// 3. 桥接失败/超时 → 推送 `error` 事件（fail-closed，不静默）
pub(crate) async fn chat_sse(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    let req: ChatRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, format!("bad request: {e}")).into_response();
        }
    };

    // 构造桥接请求
    let ask_body = LingclaudeAskRequest {
        question: req.message.clone(),
        context: req.session_id.as_deref().map(|_| format!("session={}", req.session_id.as_deref().unwrap_or(""))),
    };
    let url = format!("{}/ask/stream", state.lingclaude_base);

    let mut builder = state
        .client
        .post(&url)
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }

    let _session_id = req.session_id.clone();
    let stream = async_stream::stream! {
        match builder.json(&ask_body).send().await {
            Ok(resp) if resp.status().is_success() => {
                use futures_util::StreamExt;
                let mut sse_stream = resp.bytes_stream();
                let mut buffer = String::new();
                while let Some(chunk) = sse_stream.next().await {
                    match chunk {
                        Ok(bytes) => {
                            buffer.push_str(&String::from_utf8_lossy(&bytes));
                            // 按 SSE 事件边界（空行）切分
                            while let Some(pos) = buffer.find("\n\n") {
                                let block = buffer[..pos].to_string();
                                buffer = buffer[pos + 2..].to_string();
                                let data_line = block
                                    .lines()
                                    .find(|l| l.starts_with("data:"))
                                    .map(|l| l[5..].trim().to_string())
                                    .unwrap_or_default();
                                if data_line.is_empty() {
                                    continue;
                                }
                                let ev = Event::default().event("message").data(data_line);
                                yield Ok::<Event, std::convert::Infallible>(ev);
                            }
                        }
                        Err(e) => {
                            let ev = Event::default().event("error").data(format!(
                                "{{\"type\":\"error\",\"message\":\"stream read failed: {e}\"}}"
                            ));
                            yield Ok::<Event, std::convert::Infallible>(ev);
                            break;
                        }
                    }
                }
            }
            Ok(resp) => {
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                let ev = Event::default().event("error").data(format!(
                    "{{\"type\":\"error\",\"message\":\"lingclaude engine HTTP {status}: {text}\"}}"
                ));
                yield Ok::<Event, std::convert::Infallible>(ev);
            }
            Err(e) => {
                let ev = Event::default().event("error").data(format!(
                    "{{\"type\":\"error\",\"message\":\"lingclaude engine unreachable: {e}\"}}"
                ));
                yield Ok::<Event, std::convert::Infallible>(ev);
            }
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))).into_response()
}

// ─── permission / stop ───

/// lingclaude 8700 `/permission` 请求体（契约见 lingclaude/api.py）。
/// `reason` 可选：前端 respondPermission 只发 session_id/decision/tool_name。
#[derive(Serialize, Deserialize)]
struct PermissionRequest {
    session_id: String,
    decision: String,
    tool_name: String,
    #[serde(default)]
    reason: String,
}

/// lingclaude 8700 `/permission` 响应体。
#[derive(Deserialize)]
struct PermissionResponse {
    success: bool,
    decision: String,
}

/// `/chat/permission` 审批决策 — HTTP 桥接 lingclaude 8700 `/permission`。
///
/// 前端 `respondPermission`（api.ts L203-217）POST `{session_id, decision, tool_name}`
/// → 桥接 `{lingclaude_base}/permission` → 返回 `{success, decision}`。
/// 桥接失败返回 502（fail-closed，不静默放行）。
pub(crate) async fn chat_permission(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    let req: PermissionRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, format!("bad request: {e}")).into_response();
        }
    };

    let url = format!("{}/permission", state.lingclaude_base);
    let mut builder = state
        .client
        .post(&url)
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }

    match builder.json(&req).send().await {
        Ok(resp) if resp.status().is_success() => match resp.json::<PermissionResponse>().await {
            Ok(p) => axum::Json(serde_json::json!({ "success": p.success, "decision": p.decision })).into_response(),
            Err(e) => (StatusCode::BAD_GATEWAY, format!("bridge parse failed: {e}")).into_response(),
        },
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            (StatusCode::BAD_GATEWAY, format!("lingclaude engine HTTP {status}: {text}")).into_response()
        }
        Err(e) => (StatusCode::BAD_GATEWAY, format!("lingclaude engine unreachable: {e}")).into_response(),
    }
}

/// lingclaude 8700 `/stop` 响应体（契约待灵克补充）。
#[allow(dead_code)] // 契约待定,保留备用
#[derive(Deserialize)]
struct StopResponse {
    stopped: bool,
    final_turns: Option<u32>,
}

/// lingclaude 8700 `/sessions/{session_id}/stop` 响应体。
#[derive(Deserialize)]
struct StopSessionResponse {
    session_id: String,
    stopped: bool,
    #[serde(default)]
    final_turns: u32,
}

/// `/chat/stop` 停止会话 — HTTP 桥接 lingclaude 8700 `/sessions/{session_id}/stop`。
pub(crate) async fn chat_stop(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct StopRequest {
        session_id: Option<String>,
    }
    let req: StopRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, format!("bad request: {e}")).into_response();
        }
    };
    let session_id = match req.session_id {
        Some(id) if !id.is_empty() => id,
        _ => {
            return (StatusCode::BAD_REQUEST, "session_id is required").into_response();
        }
    };

    let base = "http://127.0.0.1:8700";
    let url = format!("{base}/sessions/{session_id}/stop");
    let mut builder = state.client.post(&url).header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }

    match builder.send().await {
        Ok(resp) if resp.status().is_success() => match resp.json::<StopSessionResponse>().await {
            Ok(s) => axum::Json(serde_json::json!({
                "stopped": s.stopped,
                "session_id": s.session_id,
                "final_turns": s.final_turns,
            })).into_response(),
            Err(e) => (StatusCode::BAD_GATEWAY, format!("bridge parse failed: {e}")).into_response(),
        },
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            (StatusCode::BAD_GATEWAY, format!("engine HTTP {status}: {text}")).into_response()
        }
        Err(e) => {
            (StatusCode::BAD_GATEWAY, format!("engine unreachable: {e}")).into_response()
        }
    }
}

/// `/mint` 端点返回一次性 token URL（webui 启动时由 CLI 侧调用并打开浏览器）。
async fn mint_token(State(state): State<AppState>) -> Response {
    let token = state.tokens.mint();
    (StatusCode::OK, format!("http://127.0.0.1:{}/?token={}", state.port, token)).into_response()
}

async fn status(State(state): State<AppState>) -> Response {
    (
        StatusCode::OK,
        axum::Json(serde_json::json!({
            "service": "lingclaude-webui",
            "port": state.port,
            "enforce_token": state.enforce_token,
            "cookie": state.cookie_name(),
            "sse": ["/chat", "/chat/stop", "/live"],
        })),
    )
        .into_response()
}

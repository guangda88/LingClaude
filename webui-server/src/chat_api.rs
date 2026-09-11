//! `/chat` SSE 流式对话 + `/chat/permission` 审批 + `/chat/stop` 停止。
//! 全部为对 lingclaude 引擎(8700 api.py)的 HTTP 桥接,失败一律 fail-closed。
//!
//! 错误事件契约：本模块产生的桥接错误统一为
//! `event: error` + 合法 JSON `{"type":"error","message":"..."}` —
//! 前端 `streamChat` 把 `type∈{done,stopped,error}` 视为终止事件。
//! 禁止手工字符串拼 JSON（错误体含引号时会产生非法 JSON 被前端静默吞掉）。

use axum::{
    extract::State,
    http::StatusCode,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
};
use serde::{Deserialize, Serialize};
use std::time::Duration;

use crate::AppState;

#[derive(Deserialize)]
struct ChatRequest {
    /// 前端传的是自身生成的 requestId，不是引擎 session_id（引擎当前无会话
    /// 存储）。接受但不消费 — 等 api.py 有真实会话语义后再接线。
    #[serde(default)]
    #[allow(dead_code)]
    session_id: Option<String>,
    message: String,
    /// 前端传 `ImageData[]` 对象数组。桥接层当前不消费；类型放宽为 Value
    /// 只保证不因附件解析失败 400（此前 Vec<String> 遇对象必 400）。
    #[serde(default)]
    #[allow(dead_code)]
    images: Vec<serde_json::Value>,
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

/// 桥接层 SSE 错误事件 — 必经 serde 构造合法 JSON（见模块注释）。
fn error_event(message: String) -> Event {
    Event::default()
        .event("error")
        .json_data(serde_json::json!({"type": "error", "message": message}))
        .expect("json! 序列化不可能失败")
}

/// `/chat` SSE 流式对话 — HTTP 桥接 lingclaude 引擎（8700 `/ask/stream`）。
///
/// 桥接协议：
/// 1. 解析前端请求 → 构造 `{question}` POST 到 `{lingclaude_base}/ask/stream`
///    （不再把 session_id 伪装成 context 注入 prompt — 引擎会把它拼进
///    `上下文：{ctx}` 污染模型输入，且每条消息 id 不同、毫无语义）
/// 2. 逐事件读取引擎 SSE 流，按事件边界（`\n\n`）切分，提取 `data:` 行原样转发
/// 3. 桥接失败/超时 → 推送合法 JSON `error` 事件（fail-closed，不静默）
pub(crate) async fn chat_sse(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    let req: ChatRequest = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, format!("bad request: {e}")).into_response();
        }
    };

    let ask_body = LingclaudeAskRequest {
        question: req.message.clone(),
        context: None, // 见 ChatRequest.session_id 注释
    };
    let url = format!("{}/ask/stream", state.lingclaude_base);

    let mut builder = state
        .client
        .post(&url)
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }

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
                            let msg = format!("stream read failed: {e}");
                            state.audit.log_event("error", &msg, None);
                            yield Ok(error_event(msg));
                            break;
                        }
                    }
                }
            }
            Ok(resp) => {
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                let msg = format!("lingclaude engine HTTP {status}: {text}");
                state.audit.log_event("error", &msg, None);
                yield Ok(error_event(msg));
            }
            Err(e) => {
                let msg = format!("lingclaude engine unreachable: {e}");
                state.audit.log_event("error", &msg, None);
                yield Ok(error_event(msg));
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

/// `/chat/stop` 停止会话 — HTTP 桥接 lingclaude 8700 `/sessions/{session_id}/stop`。
///
/// 契约现状（诚实版）：
/// - 引擎成功响应是 `Session.to_dict_redacted()`（session_id/messages/…），
///   **没有 stopped 字段** — 按 HTTP 状态判定：2xx ⇒ stopped:true。
/// - 前端传的 session_id 是自身 requestId，引擎不认识 ⇒ 通常 404 ⇒
///   `stopped:false`。真实终止语义要等引擎侧会话注册表（见协议文档 §8.2）。
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

    let url = format!("{}/sessions/{session_id}/stop", state.lingclaude_base);
    let mut builder = state.client.post(&url).header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }

    match builder.send().await {
        Ok(resp) if resp.status().is_success() => {
            // 引擎回 Session dict — 原样透传给排查用，但对外契约是 stopped 布尔
            let engine: serde_json::Value = resp.json().await.unwrap_or(serde_json::Value::Null);
            axum::Json(serde_json::json!({
                "stopped": true,
                "session_id": session_id,
                "engine": engine,
            }))
            .into_response()
        }
        Ok(resp) if resp.status() == StatusCode::NOT_FOUND => {
            // 引擎不认识该 id（当前前端传 requestId 的必然结果）— 明示未停止
            axum::Json(serde_json::json!({
                "stopped": false,
                "session_id": session_id,
                "reason": "engine has no session with this id (frontend requestId is not an engine session_id)",
            }))
            .into_response()
        }
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

//! LingClaude webUI server — AtomCode webui 复用骨架
//!
//! 复用自 AtomCode `atomcode-daemon` 的设计：
//! - rust-embed 打 dist 进二进制 + SPA fallback（webui.rs）
//! - `/?token=` 一次性 token → HttpOnly Cookie `atomcode_webui_<port>`（auth_token.rs / serve_webui_index）
//! - SSE `/chat` 流式 + `/live` 实时同步（live_api.rs 传输结构）
//!
//! 构建：`cargo build --release`；dist 由 `../webui/dist` 经 rust-embed 编入。
//! 验证：`/?token=` handoff 见 Set-Cookie；`curl -N /chat` 见 `event:` 流。

use axum::{
    extract::{Query, State},
    http::{header, StatusCode, Uri},
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse, Response,
    },
    routing::get,
    Router,
};
use futures_util::stream;
use futures_util::StreamExt;
use rust_embed::RustEmbed;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::sync::{Arc, RwLock};
use std::time::Duration;
use uuid::Uuid;

/// 前端 dist 目录（vite build 产物）——经 rust-embed 编进二进制。
#[derive(RustEmbed)]
#[folder = "../webui/dist"]
struct WebuiAssets;

/// SPA fallback：未知路径回 index.html（对齐 AtomCode `asset_or_index`）。
fn asset_or_index(path: &str) -> Option<std::borrow::Cow<'static, [u8]>> {
    let p = path.trim_start_matches('/');
    if let Some(f) = WebuiAssets::get(p) {
        return Some(f.data);
    }
    WebuiAssets::get("index.html").map(|f| f.data)
}

/// 一次性 token 存储（仅内存，进程退出失效）——对齐 AtomCode auth_token.rs。
#[derive(Default)]
struct TokenStore {
    tokens: RwLock<HashSet<String>>,
}

impl TokenStore {
    fn mint(&self) -> String {
        let t = Uuid::new_v4().to_string();
        self.tokens.write().unwrap().insert(t.clone());
        t
    }
    #[allow(dead_code)]
    fn is_valid(&self, t: &str) -> bool {
        self.tokens.read().unwrap().contains(t)
    }
    /// 一次性：验证后即移除（单次 handoff 有效）。
    fn consume(&self, t: &str) -> bool {
        let mut w = self.tokens.write().unwrap();
        w.remove(t)
    }
}

#[derive(Clone)]
struct AppState {
    tokens: Arc<TokenStore>,
    port: u16,
    enforce_token: bool,
    /// lingclaude 引擎桥接配置（HTTP → 8700 api.py）
    lingclaude_base: String,
    lingclaude_api_key: Option<String>,
    /// 桥接用 HTTP client
    client: reqwest::Client,
}

impl AppState {
    /// 防多实例互踩：cookie 名按端口作用域（RFC 6265：localhost cookie 忽略端口，
    /// 裸名会跨实例共享导致 401）——对齐 AtomCode `atomcode_webui_<port>`。
    fn cookie_name(&self) -> String {
        format!("atomcode_webui_{}", self.port)
    }
}

/// `/?token=` handoff → HttpOnly Cookie + 302（对齐 AtomCode serve_webui_index）。
async fn serve_index(
    State(state): State<AppState>,
    uri: Uri,
) -> Response {
    if state.enforce_token {
        if let Some(q) = uri.query() {
            for pair in q.split('&') {
                let mut kv = pair.splitn(2, '=');
                if kv.next() == Some("token") {
                    if let Some(raw) = kv.next() {
                        let token = raw.to_string();
                        if state.tokens.consume(&token) {
                            let rest: Vec<&str> = q.split('&').filter(|p| !p.starts_with("token=")).collect();
                            let location = if rest.is_empty() {
                                "/".to_string()
                            } else {
                                format!("/?{}", rest.join("&"))
                            };
                            let cookie = format!(
                                "{}={}; Path=/; HttpOnly; SameSite=Strict",
                                state.cookie_name(),
                                token
                            );
                            return Response::builder()
                                .status(StatusCode::FOUND)
                                .header(header::LOCATION, location)
                                .header(header::SET_COOKIE, cookie)
                                .body(axum::body::Body::empty())
                                .unwrap();
                        }
                    }
                }
            }
        }
    }
    serve_static(uri).await
}

/// 静态资源（rust-embed）+ SPA fallback。
async fn serve_static(uri: Uri) -> Response {
    let path = uri.path();
    match asset_or_index(path) {
        Some(data) => {
            let mime = mime_guess::from_path(path).first_or_octet_stream();
            Response::builder()
                .status(StatusCode::OK)
                .header(header::CONTENT_TYPE, mime.as_ref())
                .body(axum::body::Body::from(data.into_owned()))
                .unwrap()
        }
        None => Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(axum::body::Body::empty())
            .unwrap(),
    }
}

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

/// SSE 事件（对齐 AtomCode live_api 事件形状）。
#[derive(Serialize)]
struct ChatEvent {
    #[serde(rename = "type")]
    kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    input: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    output: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
}

/// lingclaude 8700 api.py 的 `/ask` 请求体（契约见 lingclaude/api.py）。
#[derive(Serialize)]
struct LingclaudeAskRequest {
    question: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    context: Option<String>,
}

/// lingclaude 8700 api.py 的 `/ask` 响应体。
#[derive(Deserialize)]
struct LingclaudeAskResponse {
    answer: String,
}

/// `/chat` SSE 流式对话 — HTTP 桥接 lingclaude 引擎（8700 `/ask`）。
///
/// 桥接协议：
/// 1. 解析前端请求 → 构造 `{question, context}` POST 到 `{lingclaude_base}/ask`
/// 2. 收到 `{answer}` 后按 AtomCode SSE 事件形状逐段推送（runtime_info → text → done）
/// 3. 桥接失败/超时 → 推送 `error` 事件（fail-closed，不静默）
async fn chat_sse(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
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
    let url = format!("{}/ask", state.lingclaude_base);

    let mut builder = state
        .client
        .post(&url)
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }

    let events: Vec<ChatEvent> = match builder.json(&ask_body).send().await {
        Ok(resp) if resp.status().is_success() => match resp.json::<LingclaudeAskResponse>().await {
            Ok(answer) => {
                // 成功：runtime_info + text(引擎回答) + done
                vec![
                    ChatEvent {
                        kind: "runtime_info".into(),
                        content: Some(format!("lingclaude@{}", state.lingclaude_base)),
                        name: None,
                        input: None,
                        output: None,
                        session_id: req.session_id.clone(),
                    },
                    ChatEvent {
                        kind: "text".into(),
                        content: Some(answer.answer),
                        name: None,
                        input: None,
                        output: None,
                        session_id: None,
                    },
                    ChatEvent {
                        kind: "done".into(),
                        content: None,
                        name: None,
                        input: None,
                        output: None,
                        session_id: req.session_id.clone(),
                    },
                ]
            }
            Err(e) => {
                vec![ChatEvent {
                    kind: "error".into(),
                    content: Some(format!("bridge response parse failed: {e}")),
                    name: None,
                    input: None,
                    output: None,
                    session_id: None,
                }]
            }
        },
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            vec![ChatEvent {
                kind: "error".into(),
                content: Some(format!("lingclaude engine HTTP {status}: {text}")),
                name: None,
                input: None,
                output: None,
                session_id: None,
            }]
        }
        Err(e) => {
            vec![ChatEvent {
                kind: "error".into(),
                content: Some(format!("lingclaude engine unreachable: {e}")),
                name: None,
                input: None,
                output: None,
                session_id: None,
            }]
        }
    };

    let stream = stream::iter(events.into_iter().map(|e| {
        Ok::<Event, std::convert::Infallible>(
            Event::default().event(&e.kind).json_data(&e).unwrap(),
        )
    }))
    .chain(stream::empty());

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))).into_response()
}

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

/// `/live` 实时状态同步 SSE — HTTP 桥接 lingclaude 引擎（8700 `/status`）。
///
/// 前端 LiveWireEvent 事件形状（api.ts L696-717）：
/// `snapshot {messages, session_id, project_hash, provider, mode}` + 增量事件。
/// 骨架先推 `snapshot`（含引擎在线状态）+ `provider`，再按周期心跳推送引擎状态；
/// 桥接失败时推 `error`（fail-closed，不静默）。
async fn live_sse(State(state): State<AppState>, Query(_q): Query<std::collections::HashMap<String, String>>) -> Response {
    // 周期心跳：每 15s 桥接一次 8700 /status 推送引擎状态
    let base = state.lingclaude_base.clone();
    let api_key = state.lingclaude_api_key.clone();
    let client = state.client.clone();

    let stream = stream::once(async move {
        let status_json = async {
            let mut builder = client.get(format!("{base}/status"));
            if let Some(key) = &api_key {
                builder = builder.header("X-API-Key", key);
            }
            match builder.send().await {
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
            }
        }
        .await;
        Ok::<Event, std::convert::Infallible>(Event::default().event("snapshot").json_data(&status_json).unwrap())
    })
    .chain(stream::repeat_with(move || {
        Ok::<Event, std::convert::Infallible>(
            Event::default()
                .event("heartbeat")
                .data(format!("{{\"ts\":{}}}", std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_secs())),
        )
    }));

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15))).into_response()
}

/// lingclaude 8700 `/permission` 请求体（契约见 lingclaude/api.py）。
#[derive(Serialize, Deserialize)]
struct PermissionRequest {
    session_id: String,
    decision: String,
    tool_name: String,
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
async fn chat_permission(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
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

/// `/?token=` mint 端点（webui 启动时由 CLI 侧调用并打开浏览器）。
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
            "sse": ["/chat", "/live"],
        })),
    )
        .into_response()
}

#[tokio::main]
async fn main() {
    let port: u16 = std::env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(13458);

    let state = AppState {
        tokens: Arc::new(TokenStore::default()),
        port,
        enforce_token: true,
        lingclaude_base: std::env::var("LINGCLAUDE_BASE")
            .unwrap_or_else(|_| "http://127.0.0.1:8700".to_string()),
        lingclaude_api_key: std::env::var("LINGCLAUDE_API_KEYS").ok(),
        client: reqwest::Client::new(),
    };

    let app = Router::new()
        .route("/", get(serve_index))
        .route("/status", get(status))
        .route("/mint", get(mint_token))
        .route("/chat", axum::routing::post(chat_sse))
        .route("/chat/permission", axum::routing::post(chat_permission))
        .route("/live", get(live_sse))
        .fallback(serve_static)
        .with_state(state.clone());

    let addr = format!("127.0.0.1:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    println!("lingclaude-webui listening on http://{addr}");
    axum::serve(listener, app).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serves_embedded_index() {
        assert!(
            WebuiAssets::get("index.html").is_some(),
            "dist/index.html 必须存在（先运行: cd webui && npm run build）"
        );
    }

    #[test]
    fn unknown_path_falls_back_to_index() {
        assert!(
            asset_or_index("some/spa/route").is_some(),
            "SPA route should fall back to index"
        );
    }

    #[test]
    fn cookie_name_is_port_scoped() {
        let state = AppState {
            tokens: Arc::new(TokenStore::default()),
            port: 13458,
            enforce_token: true,
            lingclaude_base: "http://127.0.0.1:8700".to_string(),
            lingclaude_api_key: None,
            client: reqwest::Client::new(),
        };
        assert_eq!(state.cookie_name(), "atomcode_webui_13458");
    }

    #[test]
    fn token_is_one_time() {
        let store = TokenStore::default();
        let t = store.mint();
        assert!(store.is_valid(&t));
        assert!(store.consume(&t));
        assert!(!store.is_valid(&t), "一次性 token 消费后必须失效");
    }
}

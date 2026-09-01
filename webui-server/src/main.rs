//! LingClaude webUI server — AtomCode webui 复用骨架
//!
//! F9 拆模块（原 651 行单文件）：
//! - `main.rs` — AppState + 路由 + 启动 + `/status`
//! - `webui.rs` — rust-embed 静态资源 + SPA fallback + token handoff
//! - `chat_api.rs` — `/chat` SSE + `/chat/permission` + `/chat/stop`
//! - `live_api.rs` — `/live` SSE（snapshot + 增量轮询 + 心跳）
//! - `auth.rs` — TokenStore + `/mint`
//! - `audit.rs` — AuditLogger（JSONL，尚未接线）

mod audit;
mod auth;
mod chat_api;
mod live_api;
mod webui;

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Router,
};
use std::sync::Arc;

use auth::TokenStore;

/// 防裸名 compile-time assert：cookie 名前缀必须以 `_` 结尾（port-scoped）。
const COOKIE_NAME_PREFIX: &str = "atomcode_webui_";
const fn is_port_scoped_prefix(s: &str) -> bool {
    let bytes = s.as_bytes();
    !bytes.is_empty() && bytes[bytes.len() - 1] == b'_'
}
const _: () = assert!(
    is_port_scoped_prefix(COOKIE_NAME_PREFIX),
    "cookie 名前缀必须以 _ 结尾（port-scoped），否则多实例会互踩"
);

/// axum 共享状态 — handler 模块经 `crate::AppState` 引用。
#[derive(Clone)]
pub(crate) struct AppState {
    pub(crate) tokens: Arc<TokenStore>,
    pub(crate) port: u16,
    pub(crate) enforce_token: bool,
    pub(crate) lingclaude_base: String,
    pub(crate) lingclaude_api_key: Option<String>,
    pub(crate) client: reqwest::Client,
}

impl AppState {
    /// 防多实例互踩：cookie 名按端口作用域。
    pub(crate) fn cookie_name(&self) -> String {
        format!("{}{}", COOKIE_NAME_PREFIX, self.port)
    }
}

/// `/status` — webui-server 自身状态。
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
        .route("/", get(webui::serve_index))
        .route("/status", get(status))
        .route("/mint", get(auth::mint_token))
        .route("/chat", post(chat_api::chat_sse))
        .route("/chat/stop", post(chat_api::chat_stop))
        .route("/chat/permission", post(chat_api::chat_permission))
        .route("/live", get(live_api::live_sse))
        .fallback(webui::serve_static)
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
}

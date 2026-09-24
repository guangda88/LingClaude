//! LingClaude webUI server — AtomCode webui 复用骨架
//!
//! F9 拆模块（原 651 行单文件）：
//! - `main.rs` — AppState + 路由 + 鉴权中间件 + 启动 + `/status`
//! - `webui.rs` — rust-embed 静态资源 + SPA fallback + token handoff
//! - `chat_api.rs` — `/chat` SSE + `/chat/permission` + `/chat/stop`
//! - `live_api.rs` — `/live` SSE（snapshot + 增量轮询 + 心跳）
//! - `auth.rs` — TokenStore + `/mint` + 鉴权中间件
//! - `audit.rs` — AuditLogger（JSONL；P4.2 接线：中间件请求审计 + SSE error/done 事件）

mod audit;
mod auth;
mod chat_api;
mod live_api;
mod webui;

use axum::{extract::State, http::StatusCode, response::{IntoResponse, Response}, routing::{get, post}, Router};
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
    pub(crate) audit: Arc<audit::AuditLogger>,
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
            "sse": ["/chat", "/live"],
            "post": ["/chat/stop", "/chat/permission"],
        })),
    )
        .into_response()
}

/// 组装路由 + 鉴权中间件（main 与集成测试共用）。
pub(crate) fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/", get(webui::serve_index))
        .route("/status", get(status))
        .route("/mint", get(auth::mint_token))
        .route("/chat", post(chat_api::chat_sse))
        .route("/chat/stop", post(chat_api::chat_stop))
        .route("/chat/permission", post(chat_api::chat_permission))
        .route("/live", get(live_api::live_sse))
        .fallback(webui::serve_static)
        .with_state(state.clone())
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            auth::auth_middleware,
        ))
        .layer(axum::middleware::from_fn_with_state(
            state,
            auth::host_guard,
        ))
}

#[tokio::main]
async fn main() {
    let port: u16 = std::env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(13458);

    // P4.2: 审计日志接线 — JSONL 落盘（P4 前仅在 audit.rs 定义、零调用）。
    // 路径: LINGCLAUDE_WEBUI_AUDIT_LOG 覆盖, 默认项目 .lingclaude/webui-audit.jsonl。
    let audit_path = std::env::var("LINGCLAUDE_WEBUI_AUDIT_LOG")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
            cwd.join(".lingclaude").join("webui-audit.jsonl")
        });
    if let Some(dir) = audit_path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    println!("audit log: {}", audit_path.display());

    let state = AppState {
        tokens: Arc::new(TokenStore::default()),
        audit: Arc::new(audit::AuditLogger::new(audit_path)),
        port,
        enforce_token: true,
        lingclaude_base: std::env::var("LINGCLAUDE_BASE")
            .unwrap_or_else(|_| "http://127.0.0.1:8700".to_string()),
        lingclaude_api_key: std::env::var("LINGCLAUDE_API_KEYS").ok(),
        // 连接 5s 失败快速暴露；单次读 60s 无数据视为引擎挂起（流空闲上限，
        // 引擎整 turn 都会持续吐事件，正常不会触发）。
        client: reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(5))
            .read_timeout(std::time::Duration::from_secs(60))
            .build()
            .expect("reqwest client build"),
    };

    let app = build_router(state);
    let addr = format!("127.0.0.1:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    println!("lingclaude-webui listening on http://{addr}");
    axum::serve(listener, app).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{header, Request, StatusCode};
    use tower::ServiceExt; // oneshot

    fn test_state() -> AppState {
        AppState {
            tokens: Arc::new(TokenStore::default()),
            audit: Arc::new(audit::AuditLogger::new(std::env::temp_dir().join("lingclaude-webui-test-audit.jsonl"))),
            port: 13458,
            enforce_token: true,
            lingclaude_base: "http://127.0.0.1:1".to_string(), // 测试中不应被真实访问
            lingclaude_api_key: None,
            client: reqwest::Client::new(),
        }
    }

    #[tokio::test]
    async fn api_requires_session_cookie() {
        let app = build_router(test_state());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/status")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn chat_without_cookie_is_401_json() {
        let app = build_router(test_state());
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/chat")
                    .header(header::CONTENT_TYPE, "application/json")
                    .body(axum::body::Body::from(r#"{"message":"hi"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
        let bytes = axum::body::to_bytes(resp.into_body(), 4096).await.unwrap();
        let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(v["type"], "error", "API 401 必须回 JSON，前端才能解析");
    }

    #[tokio::test]
    async fn mint_is_public_and_handoff_grants_session() {
        let state = test_state();
        // 1. /mint 无需鉴权
        let app = build_router(state.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/mint")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = axum::body::to_bytes(resp.into_body(), 4096).await.unwrap();
        let url = String::from_utf8(bytes.to_vec()).unwrap();
        let token = url
            .split("token=")
            .nth(1)
            .expect("mint 应返回带 token 的 URL")
            .to_string();

        // 2. 无 token 访问 / → 401（不再静默发页面）
        let app = build_router(state.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

        // 3. 带有效 token → 302 + Set-Cookie
        let app = build_router(state.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri(format!("/?token={token}"))
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FOUND);
        let set_cookie = resp
            .headers()
            .get(header::SET_COOKIE)
            .and_then(|v| v.to_str().ok())
            .expect("handoff 必须种会话 cookie")
            .to_string();
        assert!(set_cookie.starts_with(&state.cookie_name()));
        assert!(set_cookie.contains("HttpOnly"));
        let session = set_cookie
            .split(';')
            .next()
            .unwrap()
            .splitn(2, '=')
            .nth(1)
            .unwrap()
            .to_string();

        // 4. 带会话 cookie 访问受保护端点 → 200
        let app = build_router(state.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/status")
                    .header(header::COOKIE, format!("{}={}", state.cookie_name(), session))
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn handoff_token_is_single_use() {
        let state = test_state();
        let token = state.tokens.mint_handoff();
        let app = build_router(state.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri(format!("/?token={token}"))
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FOUND);

        // 同一 token 第二次使用 → 不再 302（无会话时中间件会 401，这里用无 token
        // 的干净请求验证 token 已被消费：新实例直接 401 即消费语义已收口于中间件）
        assert!(!state.tokens.consume_handoff(&token), "handoff token 必须一次性");
    }

    #[tokio::test]
    async fn static_assets_stay_public() {
        let app = build_router(test_state());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/index.html")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(
            resp.status(),
            StatusCode::OK,
            "静态资源不含秘密，保持公开（数据面由 API 401 保护）"
        );
    }

    #[test]
    fn cookie_name_is_port_scoped() {
        let state = test_state();
        assert_eq!(state.cookie_name(), "atomcode_webui_13458");
    }

    // ---- V7/V9 清偿（2026-09-24 双报告交叉审计）----

    #[tokio::test]
    async fn rebinding_host_gets_403_on_mint() {
        // DNS rebinding 场景：恶意网页解析到 127.0.0.1 后携带攻击者域 Host 访问。
        let app = build_router(test_state());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/mint")
                    .header(header::HOST, "evil.example.com")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN, "非 loopback Host 必须在 /mint 拿不到 token");
    }

    #[tokio::test]
    async fn loopback_and_localhost_hosts_allowed() {
        for host in ["127.0.0.1:13458", "localhost:13458", "[::1]:13458"] {
            let app = build_router(test_state());
            let resp = app
                .oneshot(
                    Request::builder()
                        .uri("/mint")
                        .header(header::HOST, host)
                        .body(axum::body::Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(resp.status(), StatusCode::OK, "合法 loopback Host 应放行: {host}");
        }
    }

    #[tokio::test]
    async fn dot_suffix_non_asset_path_requires_auth() {
        // V9：旧"含点即免鉴权"启发式下 /api/users.json 会被静默放行；
        // 表驱动白名单后未嵌入路径必须仍走会话校验（401）。
        let app = build_router(test_state());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/api/users.json")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED, "非嵌入资源的含点路径不得免鉴权");
    }

    #[tokio::test]
    async fn handoff_cookie_carries_secure_flag() {
        // V8 清偿：走真实 mint→handoff 流验证 Set-Cookie 属性集。
        let state = test_state();
        let token = state.tokens.mint_handoff();
        let app = build_router(state.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri(format!("/?token={token}"))
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::FOUND);
        let set_cookie = resp
            .headers()
            .get(header::SET_COOKIE)
            .and_then(|v| v.to_str().ok())
            .expect("handoff 必须种会话 cookie")
            .to_string();
        assert!(set_cookie.contains("Secure"), "V8 清偿：会话 cookie 必须带 Secure flag: {set_cookie}");
        assert!(set_cookie.contains("HttpOnly"));
        assert!(set_cookie.contains("SameSite=Strict"));
    }
}

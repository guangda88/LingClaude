//! 前端静态资源 + SPA fallback（对齐 AtomCode `asset_or_index`）。
//!
//! 鉴权/handoff 全部收口在 `auth::auth_middleware`：未认证请求已被 401 拦截，
//! handoff（`/?token=`）也在中间件内完成消费与 302。到达这里的请求要么持有
//! 有效会话，要么 `enforce_token=false` — handler 只管发资源。

use axum::{http::{header, StatusCode, Uri}, response::{IntoResponse, Response}};
use rust_embed::RustEmbed;

/// 前端 dist 目录（vite build 产物）——经 rust-embed 编进二进制。
#[derive(RustEmbed)]
#[folder = "../webui/dist"]
struct WebuiAssets;

/// SPA fallback：未知路径回 index.html；带扩展名的未知路径回 404。
pub(crate) fn asset_or_index(path: &str) -> Option<std::borrow::Cow<'static, [u8]>> {
    let p = path.trim_start_matches('/');
    if let Some(f) = WebuiAssets::get(p) {
        return Some(f.data);
    }
    WebuiAssets::get("index.html").map(|f| f.data)
}

/// 前端会调用、但 webui-server 未实现的 API 路径前缀。
/// 对它们回 404 JSON 而非 index.html — 否则前端 `resp.json()` 拿到 HTML
/// 抛 SyntaxError，真实原因（端点不存在）被掩盖。
/// （副作用：在这些路径上硬刷新 SPA 会得到 404 而非页面 — 可接受，显式优于掩盖。）
const UNIMPLEMENTED_API_PREFIXES: &[&str] = &[
    "/auth", "/approval_mode", "/command", "/sessions", "/config", "/fs/", "/live/",
];

fn is_unimplemented_api(path: &str) -> bool {
    UNIMPLEMENTED_API_PREFIXES.iter().any(|p| path.starts_with(p))
}

/// 静态资源（rust-embed）+ SPA fallback。鉴权由 `auth_middleware` 统一处理。
pub(crate) async fn serve_static(uri: Uri) -> Response {
    let path = uri.path();
    if is_unimplemented_api(path) {
        return (
            StatusCode::NOT_FOUND,
            axum::Json(serde_json::json!({
                "type": "error",
                "message": format!("endpoint not implemented in webui-server: {path}"),
            })),
        )
            .into_response();
    }
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

/// `/` — 主页面（会话校验在中间件）。
pub(crate) async fn serve_index(uri: Uri) -> Response {
    serve_static(uri).await
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
    fn frontend_api_calls_get_explicit_404() {
        assert!(is_unimplemented_api("/auth/status"));
        assert!(is_unimplemented_api("/sessions"));
        assert!(is_unimplemented_api("/sessions/abc/stop"));
        assert!(is_unimplemented_api("/live/message"));
        assert!(!is_unimplemented_api("/chat")); // 已实现路由，不走 fallback
        assert!(!is_unimplemented_api("/assets/app.js"));
    }
}

//! 前端静态资源 + SPA fallback + `/?token=` handoff。

use axum::{
    extract::State,
    http::{header, StatusCode, Uri},
    response::Response,
};
use rust_embed::RustEmbed;

use crate::AppState;

/// 前端 dist 目录（vite build 产物）——经 rust-embed 编进二进制。
#[derive(RustEmbed)]
#[folder = "../webui/dist"]
struct WebuiAssets;

/// SPA fallback：未知路径回 index.html（对齐 AtomCode `asset_or_index`）。
pub(crate) fn asset_or_index(path: &str) -> Option<std::borrow::Cow<'static, [u8]>> {
    let p = path.trim_start_matches('/');
    if let Some(f) = WebuiAssets::get(p) {
        return Some(f.data);
    }
    WebuiAssets::get("index.html").map(|f| f.data)
}

/// 静态资源（rust-embed）+ SPA fallback。
pub(crate) async fn serve_static(uri: Uri) -> Response {
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

/// `/?token=` handoff → HttpOnly Cookie + 302（对齐 AtomCode serve_webui_index）。
pub(crate) async fn serve_index(State(state): State<AppState>, uri: Uri) -> Response {
    if state.enforce_token {
        if let Some(q) = uri.query() {
            for pair in q.split('&') {
                let mut kv = pair.splitn(2, '=');
                if kv.next() == Some("token") {
                    if let Some(raw) = kv.next() {
                        let token = raw.to_string();
                        if state.tokens.consume(&token) {
                            let rest: Vec<&str> = q
                                .split('&')
                                .filter(|p| !p.starts_with("token="))
                                .collect();
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
}

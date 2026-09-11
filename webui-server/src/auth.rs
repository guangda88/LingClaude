//! 鉴权 — 一次性 handoff token + 会话 token 存储与 `/mint` 端点。
//!
//! 认证流（对齐 webui-v0.1.md §六）：
//! 1. CLI 调 `/mint` 得到 `http://127.0.0.1:{port}/?token=<handoff>`
//! 2. 浏览器访问该 URL → `serve_index` 消费一次性 token（5 分钟 TTL）
//! 3. 种下 **会话 cookie**（24h TTL，HttpOnly/SameSite=Strict）→ 302 到去参地址
//! 4. 之后所有非静态请求经 `auth_middleware` 校验会话 cookie，失败 401

use axum::{
    extract::{Request, State},
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use std::collections::HashMap;
use std::sync::RwLock;
use std::time::{Duration, Instant};
use uuid::Uuid;

use crate::AppState;

/// 一次性 handoff token 有效期 — 用户点击 CLI 输出的链接足够了。
pub(crate) const HANDOFF_TTL: Duration = Duration::from_secs(300);
/// 会话 cookie 有效期 — 24h 绝对过期（不滑动续期，到期重新走 /mint）。
pub(crate) const SESSION_TTL: Duration = Duration::from_secs(24 * 3600);
/// 未消费 handoff token 上限 — 防 drive-by GET /mint 缓慢膨胀内存。
const MAX_HANDOFF_TOKENS: usize = 128;
/// 活跃会话上限 — 本机单用户场景的兜底。
const MAX_SESSIONS: usize = 256;

/// token 存储（仅内存，进程退出全部失效）。
#[derive(Default)]
pub(crate) struct TokenStore {
    /// 一次性 handoff token → 过期时刻
    handoff: RwLock<HashMap<String, Instant>>,
    /// 会话 token（cookie 值）→ 过期时刻
    sessions: RwLock<HashMap<String, Instant>>,
}

fn purge_expired(map: &mut HashMap<String, Instant>, now: Instant) {
    map.retain(|_, exp| *exp > now);
}

impl TokenStore {
    /// 签发一次性 handoff token（TTL 内未消费自动失效，超量先逐过期再逐最旧）。
    pub(crate) fn mint_handoff(&self) -> String {
        let t = Uuid::new_v4().to_string();
        let mut w = self.handoff.write().unwrap();
        let now = Instant::now();
        purge_expired(&mut w, now);
        if w.len() >= MAX_HANDOFF_TOKENS {
            if let Some(oldest) = w.iter().min_by_key(|(_, exp)| **exp).map(|(k, _)| k.clone()) {
                w.remove(&oldest);
            }
        }
        w.insert(t.clone(), now + HANDOFF_TTL);
        t
    }

    /// 一次性：消费即移除。
    pub(crate) fn consume_handoff(&self, t: &str) -> bool {
        let mut w = self.handoff.write().unwrap();
        purge_expired(&mut w, Instant::now());
        if w.remove(t).is_some() {
            true
        } else {
            false
        }
    }

    /// 认证通过后签发会话 token（handoff 消费成功时调用）。
    pub(crate) fn mint_session(&self) -> String {
        let t = Uuid::new_v4().to_string();
        let mut w = self.sessions.write().unwrap();
        let now = Instant::now();
        purge_expired(&mut w, now);
        if w.len() >= MAX_SESSIONS {
            if let Some(oldest) = w.iter().min_by_key(|(_, exp)| **exp).map(|(k, _)| k.clone()) {
                w.remove(&oldest);
            }
        }
        w.insert(t.clone(), now + SESSION_TTL);
        t
    }

    /// 中间件校验会话 cookie。
    pub(crate) fn validate_session(&self, t: &str) -> bool {
        let mut w = self.sessions.write().unwrap();
        purge_expired(&mut w, Instant::now());
        match w.get(t) {
            Some(exp) if *exp > Instant::now() => true,
            _ => false,
        }
    }

    /// 供未来 /logout 使用。
    #[allow(dead_code)]
    pub(crate) fn revoke_session(&self, t: &str) -> bool {
        self.sessions.write().unwrap().remove(t).is_some()
    }

    #[cfg(test)]
    pub(crate) fn counts(&self) -> (usize, usize) {
        (self.handoff.read().unwrap().len(), self.sessions.read().unwrap().len())
    }
}

/// `/mint` 端点返回一次性 token URL（webui 启动时由 CLI 侧调用并打开浏览器）。
/// 无鉴权是设计使然 — token 本身就是凭证；TTL+上限兜底防滥用。
pub(crate) async fn mint_token(State(state): State<AppState>) -> Response {
    let token = state.tokens.mint_handoff();
    (
        StatusCode::OK,
        format!("http://127.0.0.1:{}/?token={}", state.port, token),
    )
        .into_response()
}

/// 从 Cookie 头提取指定 cookie 的值。
fn extract_cookie_value(cookie_header: &str, name: &str) -> Option<String> {
    for pair in cookie_header.split(';') {
        let mut kv = pair.trim().splitn(2, '=');
        if kv.next() == Some(name) {
            return kv.next().map(|v| v.to_string());
        }
    }
    None
}

/// 取 query 中 `token=` 参数值（handoff 凭证）。
fn query_token(query: &str) -> Option<String> {
    for pair in query.split('&') {
        let mut kv = pair.splitn(2, '=');
        if kv.next() == Some("token") {
            if let Some(v) = kv.next() {
                return Some(v.to_string());
            }
        }
    }
    None
}

/// 静态资源路径判定：最后一段含 `.`（如 /assets/index-a1b2.js）。
/// 这类资源不含秘密，公开；页面与 API 一律要求会话。
fn is_static_asset(path: &str) -> bool {
    path != "/" && path.rsplit('/').next().is_some_and(|seg| seg.contains('.'))
}

/// handoff 成功：签发会话 cookie + 302 到去参地址。
fn handoff_grant(state: &AppState, query: &str) -> Response {
    let session = state.tokens.mint_session();
    let rest: Vec<&str> = query.split('&').filter(|p| !p.starts_with("token=")).collect();
    let location = if rest.is_empty() {
        "/".to_string()
    } else {
        format!("/?{}", rest.join("&"))
    };
    let cookie = format!(
        "{}={}; Path=/; HttpOnly; SameSite=Strict; Max-Age={}",
        state.cookie_name(),
        session,
        SESSION_TTL.as_secs(),
    );
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, location)
        .header(header::SET_COOKIE, cookie)
        .body(axum::body::Body::empty())
        .unwrap()
}

/// 鉴权中间件 — `enforce_token=true` 时除 `/mint` 与静态资源外全部要求会话 cookie。
///
/// handoff 也在本中间件单点收口：`/?token=<有效一次性 token>` 在此消费并签发
/// 会话（handler 层不再触碰 token，杜绝双重消费/校验漂移）。
pub(crate) async fn auth_middleware(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Response {
    let path = req.uri().path().to_string();
    let query = req.uri().query().unwrap_or("").to_string();
    let method = req.method().to_string();
    if !state.enforce_token || path == "/mint" || is_static_asset(&path) {
        let resp = next.run(req).await;
        // P4.2: 免鉴权路径同样记审计（status + 4 段字段）。
        state.audit.log_request(&method, &path, None, resp.status().as_u16(), None);
        return resp;
    }

    // `/` 上的 handoff：一次性 token 换会话 cookie。token 无效不直接拒 —
    // 已有会话的用户点过期链接仍应进入（落到下方会话校验）。
    let mut handoff_attempted = false;
    if path == "/" {
        if let Some(tok) = query_token(&query) {
            handoff_attempted = true;
            if state.tokens.consume_handoff(&tok) {
                return handoff_grant(&state, &query);
            }
        }
    }

    let cookie_ok = req
        .headers()
        .get(header::COOKIE)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| extract_cookie_value(v, &state.cookie_name()))
        .map(|v| state.tokens.validate_session(&v))
        .unwrap_or(false);
    if cookie_ok {
        let resp = next.run(req).await;
        state.audit.log_request(&method, &path, None, resp.status().as_u16(), None);
        return resp;
    }
    // P4.2: 鉴权失败审计（error 字段标记 auth_failed）
    state.audit.log_request(&method, &path, None, 401, Some("auth_failed"));

    // handoff 尝试失败且无会话：302 回干净的 / 让用户看到 401 页（而非把
    // token 留在地址栏反复重试）。
    if handoff_attempted {
        return Response::builder()
            .status(StatusCode::FOUND)
            .header(header::LOCATION, "/")
            .body(axum::body::Body::empty())
            .unwrap();
    }

    // API 路径回 JSON，页面路径回可读 HTML — 都是 401（对齐 webui-v0.1.md §六）。
    let is_api = path.starts_with("/chat") || path.starts_with("/live") || path == "/status";
    if is_api {
        (
            StatusCode::UNAUTHORIZED,
            axum::Json(serde_json::json!({
                "type": "error",
                "message": "unauthorized: missing or invalid webui session cookie",
            })),
        )
            .into_response()
    } else {
        (
            StatusCode::UNAUTHORIZED,
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                (header::CACHE_CONTROL, "no-store"),
            ],
            "401 — 需要访问令牌。请使用 CLI 启动时输出的带 token 链接进入。",
        )
            .into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handoff_token_is_one_time() {
        let store = TokenStore::default();
        let t = store.mint_handoff();
        assert_eq!(store.counts().0, 1);
        assert!(store.consume_handoff(&t));
        assert!(!store.consume_handoff(&t), "一次性 token 消费后必须失效");
        assert_eq!(store.counts().0, 0);
    }

    #[test]
    fn handoff_cap_bounds_memory() {
        let store = TokenStore::default();
        for _ in 0..(MAX_HANDOFF_TOKENS + 50) {
            store.mint_handoff();
        }
        let (n, _) = store.counts();
        assert!(
            n <= MAX_HANDOFF_TOKENS,
            "未消费 token 数必须被上限约束，实际 {n}"
        );
    }

    #[test]
    fn session_flow_mint_validate_revoke() {
        let store = TokenStore::default();
        let s = store.mint_session();
        assert!(store.validate_session(&s));
        assert!(!store.validate_session("bogus"));
        assert!(store.revoke_session(&s));
        assert!(!store.validate_session(&s), "revoke 后必须失效");
    }

    #[test]
    fn cookie_value_extraction() {
        let header = "a=1; atomcode_webui_13458=tok; b=2";
        assert_eq!(
            extract_cookie_value(header, "atomcode_webui_13458").as_deref(),
            Some("tok")
        );
        assert_eq!(extract_cookie_value(header, "missing"), None);
    }

    #[test]
    fn static_asset_detection() {
        assert!(is_static_asset("/assets/index-a1b2.js"));
        assert!(is_static_asset("/favicon.ico"));
        assert!(!is_static_asset("/"));
        assert!(!is_static_asset("/chat"));
        assert!(!is_static_asset("/sessions/abc"));
    }
}

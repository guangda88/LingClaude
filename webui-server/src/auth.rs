//! 一次性 token 存储与 `/mint` 端点 — 对齐 AtomCode auth_token.rs。

use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use std::collections::HashSet;
use std::sync::RwLock;
use uuid::Uuid;

use crate::AppState;

/// 一次性 token 存储（仅内存，进程退出失效）。
#[derive(Default)]
pub(crate) struct TokenStore {
    tokens: RwLock<HashSet<String>>,
}

impl TokenStore {
    pub(crate) fn mint(&self) -> String {
        let t = Uuid::new_v4().to_string();
        self.tokens.write().unwrap().insert(t.clone());
        t
    }
    #[allow(dead_code)] // 仅供测试与未来校验路径使用
    pub(crate) fn is_valid(&self, t: &str) -> bool {
        self.tokens.read().unwrap().contains(t)
    }
    /// 一次性：验证后即移除（单次 handoff 有效）。
    pub(crate) fn consume(&self, t: &str) -> bool {
        let mut w = self.tokens.write().unwrap();
        w.remove(t)
    }
}

/// `/mint` 端点返回一次性 token URL（webui 启动时由 CLI 侧调用并打开浏览器）。
pub(crate) async fn mint_token(State(state): State<AppState>) -> Response {
    let token = state.tokens.mint();
    (
        StatusCode::OK,
        format!("http://127.0.0.1:{}/?token={}", state.port, token),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_one_time() {
        let store = TokenStore::default();
        let t = store.mint();
        assert!(store.is_valid(&t));
        assert!(store.consume(&t));
        assert!(!store.is_valid(&t), "一次性 token 消费后必须失效");
    }
}

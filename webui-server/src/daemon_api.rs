//! daemon 化补齐端点 — 对齐 AtomCode webUI 模式（前端 35 端点契约）。
//!
//! 分三类实现：
//! 1. **真实现**：sessions/projects CRUD（sessions_store）、/cd、/fs/*、
//!    /approval_mode、/config、/models（读 config.yaml）、/tunnel/status
//! 2. **引擎桥接**：/permission/mode（8700 端点转发）、/live/stop、/live/permission
//! 3. **诚实降级**：无引擎对应能力的端点返回契约内形状 + 明确语义
//!    （禁止 404 — 前端把 404 当故障弹错）。
//!
//! 全局状态（审批模式/当前项目/活跃会话/广播）挂 AppState.proc，
//! 同进程多 Router 克隆共享；测试各自 new() 互不干扰。

use axum::{
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::IntoResponse,
    response::Response,
    Json,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, RwLock};

use crate::sessions_store as store;
use crate::AppState;

// ─── 进程级状态 ───────────────────────────────────────────────────────────

/// 活跃会话表 — session_id → 最近活跃秒级时间戳（/chat done 时移除）。
pub(crate) type ActiveChats = Arc<Mutex<HashMap<String, u64>>>;

#[derive(Clone)]
pub(crate) struct ProcessState {
    /// webui 审批模式（前端 ApprovalMode：build/accept_edits/plan/bypass）。
    approval_mode: Arc<RwLock<String>>,
    /// 当前项目（daemon 语义的 cwd，对齐 atomcode ProjectState）。
    project: Arc<RwLock<ProjectState>>,
    /// 活跃会话（/chat 请求中 → done 结束）。
    pub(crate) active_chats: ActiveChats,
    /// /live/message 广播（sync 模式跨标签页事件流）。
    pub(crate) bus: Arc<tokio::sync::broadcast::Sender<Arc<str>>>,
    /// 监听地址（/tunnel/status reachable 判定）。
    pub(crate) bind_host: Arc<String>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ProjectState {
    pub(crate) working_dir: String,
    previous_dir: Option<String>,
    recent_dirs: Vec<String>,
    name: String,
    pub(crate) project_hash: String,
}

impl ProcessState {
    pub(crate) fn new(bind_host: String) -> Self {
        let cwd = std::env::current_dir()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "/".into());
        let name = std::path::Path::new(&cwd)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "project".into());
        Self {
            approval_mode: Arc::new(RwLock::new("build".into())),
            project: Arc::new(RwLock::new(ProjectState {
                working_dir: cwd.clone(),
                previous_dir: None,
                recent_dirs: vec![cwd],
                name,
                project_hash: store::project_hash(&cwd_placeholder()),
            })),
            active_chats: Arc::new(Mutex::new(HashMap::new())),
            bus: Arc::new(tokio::sync::broadcast::channel(256).0),
            bind_host: Arc::new(bind_host),
        }
    }

    fn update_project(&self, new_path: &str) -> ProjectState {
        let mut p = self.project.write().unwrap();
        if p.working_dir != new_path {
            let old = p.working_dir.clone();
            p.previous_dir = Some(old);
            p.working_dir = new_path.to_string();
            p.name = std::path::Path::new(new_path)
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_else(|| "project".into());
            p.recent_dirs.retain(|d| d != new_path);
            p.recent_dirs.insert(0, new_path.to_string());
            p.recent_dirs.truncate(5);
        }
        p.project_hash = store::project_hash(&p.working_dir);
        p.clone()
    }

    pub(crate) fn current_project(&self) -> ProjectState {
        self.project.read().unwrap().clone()
    }

    pub(crate) fn approval_mode(&self) -> String {
        self.approval_mode.read().unwrap().clone()
    }

    /// 广播事件到 /live SSE 总线（订阅者全走同一通道）。
    pub(crate) fn publish(&self, event: serde_json::Value) {
        let Ok(text) = serde_json::to_string(&event) else { return };
        let _ = self.bus.send(Arc::from(text));
    }

    /// 标记会话进入活跃态（/chat 请求开始）。
    pub(crate) fn mark_active(&self, session_id: &str) {
        self.active_chats
            .lock()
            .unwrap()
            .insert(session_id.to_string(), now_secs());
    }

    /// 会话活跃结算（done/stop/失败路径统一收口）。
    pub(crate) fn clear_active(&self, session_id: &str) {
        self.active_chats.lock().unwrap().remove(session_id);
    }

    /// 活跃会话 id 列表（/chat/active 消费）。
    pub(crate) fn active_ids(&self) -> Vec<String> {
        self.active_chats.lock().unwrap().keys().cloned().collect()
    }

    /// 订阅广播总线（/live SSE 每连接一个 Receiver）。
    pub(crate) fn subscribe(&self) -> tokio::sync::broadcast::Receiver<Arc<str>> {
        self.bus.subscribe()
    }
}

/// 初始 project_hash 的占位 — new() 里 cwd 尚未规范化时的稳定值。
fn cwd_placeholder() -> String {
    std::env::current_dir()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| "/".into())
}

fn ok_json(v: serde_json::Value) -> Response {
    (StatusCode::OK, Json(v)).into_response()
}

fn err_msg(status: StatusCode, msg: impl std::fmt::Display) -> Response {
    (status, msg.to_string()).into_response()
}

fn store_err(e: String) -> Response {
    err_msg(StatusCode::INTERNAL_SERVER_ERROR, e)
}

// ─── sessions / projects（真实现） ────────────────────────────────────────

/// GET /sessions — 最近 50 条跨项目。
pub(crate) async fn sessions_get(State(state): State<AppState>) -> Response {
    match store::list_recent_sessions(&state.store_root) {
        Ok(list) => ok_json(serde_json::to_value(list).unwrap_or(json!([]))),
        Err(e) => store_err(e),
    }
}

/// POST /sessions — 新建会话 `{working_dir?, title?}`。
pub(crate) async fn sessions_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct CreateReq {
        working_dir: Option<String>,
        title: Option<String>,
    }
    let req: CreateReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let working_dir = req
        .working_dir
        .unwrap_or_else(|| state.proc.current_project().working_dir);
    if !std::path::Path::new(&working_dir).exists() {
        return err_msg(
            StatusCode::BAD_REQUEST,
            format!("Working directory does not exist: {working_dir}"),
        );
    }
    state.proc.update_project(&working_dir);
    match store::create_session(&state.store_root, &working_dir, req.title) {
        Ok(d) => ok_json(json!({
            "id": d.id,
            "name": d.name,
            "working_dir": d.working_dir,
            "project_hash": store::project_hash(&working_dir),
            "created_at": d.created_at,
        })),
        Err(e) => store_err(e),
    }
}

/// GET /sessions/search?q=
pub(crate) async fn sessions_search(
    State(state): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let needle = q.get("q").cloned().unwrap_or_default();
    if needle.trim().is_empty() {
        return err_msg(StatusCode::BAD_REQUEST, "Search keyword cannot be empty");
    }
    match store::search_sessions(&state.store_root, &needle) {
        Ok(list) => ok_json(serde_json::to_value(list).unwrap_or(json!([]))),
        Err(e) => store_err(e),
    }
}

/// GET /sessions/resolve/:id — 无匹配 404（前端 404 → null 语义）。
pub(crate) async fn sessions_resolve(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Response {
    match store::resolve_session(&state.store_root, &id) {
        Ok(Some(m)) => ok_json(serde_json::to_value(m).unwrap_or(json!(null))),
        Ok(None) => err_msg(StatusCode::NOT_FOUND, format!("session not found: {id}")),
        Err(e) => store_err(e),
    }
}

/// GET /chat/active — 活跃会话 id 列表（前端聊天页轮询）。
pub(crate) async fn chat_active_get(State(state): State<AppState>) -> Response {
    ok_json(json!(state.proc.active_ids()))
}

/// GET /project — 当前项目状态。
pub(crate) async fn project_get(State(state): State<AppState>) -> Response {
    let p = state.proc.current_project();
    ok_json(serde_json::to_value(p).unwrap_or(json!(null)))
}

/// POST /cd `{path, set_default?}` — 切换 daemon 工作目录。
pub(crate) async fn cd_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct CdReq {
        path: String,
    }
    let req: CdReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let expanded = expand_tilde(&req.path);
    if !std::path::Path::new(&expanded).is_dir() {
        return ok_json(json!({
            "success": false,
            "message": format!("not a directory: {expanded}"),
            "current_dir": state.proc.current_project().working_dir,
            "project_hash": state.proc.current_project().project_hash,
        }));
    }
    let p = state.proc.update_project(&expanded);
    ok_json(json!({
        "success": true,
        "message": format!("switched to {expanded}"),
        "current_dir": p.working_dir,
        "project_hash": p.project_hash,
    }))
}

/// GET /projects — 项目聚合。
pub(crate) async fn projects_get(State(state): State<AppState>) -> Response {
    match store::list_projects(&state.store_root) {
        Ok(list) => ok_json(serde_json::to_value(list).unwrap_or(json!([]))),
        Err(e) => store_err(e),
    }
}

/// GET /projects/:hash/sessions — 单桶会话（裸 SessionMeta[]）。
pub(crate) async fn project_sessions_get(
    State(state): State<AppState>,
    Path(hash): Path<String>,
) -> Response {
    match store::list_project_sessions(&state.store_root, &hash) {
        Ok(list) => ok_json(serde_json::to_value(list).unwrap_or(json!([]))),
        Err(e) => store_err(e),
    }
}

/// GET /projects/:hash/sessions/:id — 会话详情（含 messages）。
pub(crate) async fn project_session_detail_get(
    State(state): State<AppState>,
    Path((hash, id)): Path<(String, String)>,
) -> Response {
    match store::get_session_detail(&state.store_root, &hash, &id) {
        Some(d) => ok_json(serde_json::to_value(d).unwrap_or(json!(null))),
        None => err_msg(StatusCode::NOT_FOUND, format!("session not found: {id}")),
    }
}

/// PATCH /projects/:hash/sessions/:id — 重命名 `{name}`。
pub(crate) async fn project_session_patch(
    State(state): State<AppState>,
    Path((hash, id)): Path<(String, String)>,
    body: axum::body::Bytes,
) -> Response {
    #[derive(Deserialize)]
    struct RenameReq {
        name: String,
    }
    let req: RenameReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    match store::rename_session(&state.store_root, &hash, &id, &req.name) {
        Ok(true) => ok_json(json!({"ok": true})),
        Ok(false) => err_msg(StatusCode::NOT_FOUND, format!("session not found: {id}")),
        Err(e) => store_err(e),
    }
}

/// DELETE /projects/:hash/sessions/:id
pub(crate) async fn project_session_delete(
    State(state): State<AppState>,
    Path((hash, id)): Path<(String, String)>,
) -> Response {
    match store::delete_session(&state.store_root, &hash, &id) {
        Ok(_) => ok_json(json!({"ok": true})),
        Err(e) => store_err(e),
    }
}

/// DELETE /projects/:hash — 删除项目桶。
pub(crate) async fn project_delete(
    State(state): State<AppState>,
    Path(hash): Path<String>,
) -> Response {
    match store::delete_project(&state.store_root, &hash) {
        Ok(_) => ok_json(json!({"ok": true})),
        Err(e) => store_err(e),
    }
}

// ─── 审批模式 / permission mode ──────────────────────────────────────────

/// GET /approval_mode — webui 审批模式（进程态）。
pub(crate) async fn approval_mode_get(State(state): State<AppState>) -> Response {
    ok_json(json!({"ok": true, "mode": state.proc.approval_mode()}))
}

/// POST /approval_mode `{mode}` — 校验 build/accept_edits/plan/bypass。
pub(crate) async fn approval_mode_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct ModeReq {
        mode: String,
    }
    let req: ModeReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let valid = ["build", "accept_edits", "plan", "bypass"];
    if !valid.contains(&req.mode.as_str()) {
        return ok_json(json!({
            "ok": false,
            "error": format!("invalid mode: {} (allowed: {valid:?})", req.mode),
        }));
    }
    *state.proc.approval_mode.write().unwrap() = req.mode.clone();
    state.proc.publish(json!({"type": "mode", "mode": req.mode}));
    ok_json(json!({"ok": true, "mode": req.mode}))
}

/// GET /permission/mode — 引擎全局权限模式（auto/ask/strict）桥接。
pub(crate) async fn permission_mode_get(State(state): State<AppState>) -> Response {
    bridge_get_json(state, "/permission/mode").await
}

/// POST /permission/mode `{mode}` — 引擎全局权限模式设置桥接。
pub(crate) async fn permission_mode_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    bridge_post_json(state, "/permission/mode", &body).await
}

// ─── config / models / skills / mcp / tunnel ─────────────────────────────

/// lc 模型视图 — cli 启动时经 `LINGCLAUDE_WEBUI_MODEL` 注入（JSON，Python 侧
/// 解析 config.yaml，Rust 不引 YAML 依赖）。缺省时 /config /models 返回空集。
fn injected_model() -> Option<serde_json::Value> {
    let raw = std::env::var("LINGCLAUDE_WEBUI_MODEL").ok()?;
    serde_json::from_str(&raw).ok()
}

/// GET /config — ConfigInfo 契约（永不泄漏 api_key：只给 has_api_key 布尔）。
pub(crate) async fn config_get() -> Response {
    let Some(m) = injected_model() else {
        return ok_json(json!({
            "path": "config.yaml", "default_provider": "", "providers": [],
        }));
    };
    let config_path = std::env::var("LINGCLAUDE_CONFIG").unwrap_or_else(|_| "config.yaml".into());
    ok_json(json!({
        "path": config_path,
        "default_provider": m["provider"],
        "providers": [{
            "name": m["provider"],
            "type": m["provider"],
            "model": m["model"],
            "base_url": m.get("base_url").cloned().unwrap_or(serde_json::Value::Null),
            "has_api_key": m["has_api_key"],
            "is_default": true,
        }],
    }))
}

/// POST /config/reload — 配置每次请求现读（无缓存），reload 是语义 no-op。
pub(crate) async fn config_reload_post() -> Response {
    ok_json(json!({"ok": true, "reloaded": true}))
}

/// GET /models — config.yaml 的模型目录（ModelInfo[]，数据源 env 注入）。
pub(crate) async fn models_get() -> Response {
    let Some(m) = injected_model() else {
        return ok_json(json!([]));
    };
    if m["model"].as_str().unwrap_or("").is_empty() {
        return ok_json(json!([]));
    }
    ok_json(json!([{
        "provider": m["provider"],
        "model": m["model"],
        "provider_type": m["provider"],
        "is_default": true,
        // lc 引擎无 DeepSeek reasoning_effort 语义 — 选择器隐藏
        "effort_applicable": false,
    }]))
}

/// GET /skills — 扫描 ~/.lingclaude/skills/*/（目录名即技能名）。
pub(crate) async fn skills_get() -> Response {
    let mut out = Vec::new();
    if let Ok(home) = std::env::var("HOME") {
        let dir = std::path::PathBuf::from(home).join(".lingclaude").join("skills");
        if let Ok(entries) = std::fs::read_dir(&dir) {
            for e in entries.flatten() {
                let p = e.path();
                if p.is_dir() {
                    out.push(json!({
                        "name": e.file_name().to_string_lossy(),
                        "description": "",
                    }));
                }
            }
        }
    }
    ok_json(json!(out))
}

/// GET /mcp/status — lc webui 桥不管理 MCP 进程：空集 + 默认信任。
pub(crate) async fn mcp_status_get() -> Response {
    ok_json(json!({"servers": [], "trusted": true, "blocked": []}))
}

/// GET /tunnel/status — bind 可达性 + 远程 URL（复用请求 token 拼接）。
pub(crate) async fn tunnel_status_get(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let bind_host = state.proc.bind_host.as_str();
    let loopback = matches!(bind_host, "127.0.0.1" | "localhost" | "::1");
    let reachable = !loopback;
    let token = token_from_request(&state, &headers);
    let remote_url = if reachable {
        token.map(|t| format!("http://{bind_host}:{}/?token={t}", state.port))
    } else {
        None
    };
    ok_json(json!({
        "bind_host": bind_host,
        "port": state.port,
        "reachable": reachable,
        "pgy": {"installed": false, "ipv4": null},
        "remote_url": remote_url,
        "qr_svg": null,
    }))
}

/// 从 Cookie 或 Authorization 头提取 webui token（/tunnel/status 拼远程 URL 用）。
fn token_from_request(state: &AppState, headers: &HeaderMap) -> Option<String> {
    if let Some(auth) = headers.get(header::AUTHORIZATION).and_then(|h| h.to_str().ok()) {
        if let Some(t) = auth.strip_prefix("Bearer ") {
            return Some(t.trim().to_string());
        }
    }
    let cookie = headers.get(header::COOKIE).and_then(|h| h.to_str().ok())?;
    let name = state.cookie_name();
    for pair in cookie.split(';') {
        let pair = pair.trim();
        if let Some(v) = pair.strip_prefix(&format!("{name}=")) {
            return Some(v.to_string());
        }
    }
    None
}

// ─── fs / command ────────────────────────────────────────────────────────

fn expand_tilde(p: &str) -> String {
    if let Some(rest) = p.strip_prefix('~') {
        if let Ok(home) = std::env::var("HOME") {
            return home + rest.trim_start_matches('/');
        }
    }
    p.to_string()
}

/// GET /fs/list?path= — 目录直读（dirs + files，跳过隐藏项）。
pub(crate) async fn fs_list(Query(q): Query<HashMap<String, String>>) -> Response {
    let raw = q.get("path").cloned().unwrap_or_else(|| ".".into());
    let dir = expand_tilde(&raw);
    let mut dirs = Vec::new();
    let mut files = Vec::new();
    match std::fs::read_dir(&dir) {
        Ok(entries) => {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                if name.starts_with('.') {
                    continue;
                }
                if e.path().is_dir() {
                    dirs.push(name);
                } else {
                    files.push(name);
                }
            }
            dirs.sort();
            files.sort();
            ok_json(json!({"path": dir, "dirs": dirs, "files": files}))
        }
        Err(e) => err_msg(StatusCode::BAD_REQUEST, format!("cannot list {dir}: {e}")),
    }
}

/// POST /fs/mkdir `{path}` — 递归建目录。
pub(crate) async fn fs_mkdir(body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct MkdirReq {
        path: String,
    }
    let req: MkdirReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let dir = expand_tilde(&req.path);
    match std::fs::create_dir_all(&dir) {
        Ok(()) => ok_json(json!({"path": dir})),
        Err(e) => err_msg(StatusCode::BAD_REQUEST, format!("mkdir failed: {e}")),
    }
}

/// POST /command — TUI 斜杠命令子集；webui 桥未支持的命令返回 error 形状
/// （契约内诚实失败，不 500 不 404）。
pub(crate) async fn command_post(body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct CmdReq {
        command: String,
        #[serde(default)]
        #[allow(dead_code)]
        arg: Option<String>,
    }
    let req: CmdReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    match req.command.as_str() {
        "clear" | "help" => ok_json(json!({
            "kind": "error",
            "message": format!("/{ } 由前端本地处理，无需服务端", req.command),
        })),
        _ => ok_json(json!({
            "kind": "error",
            "message": format!("命令 /{} 暂不支持（webUI 为 TUI 命令子集）", req.command),
        })),
    }
}

// ─── live 桥接族（sync 模式） ─────────────────────────────────────────────

#[derive(Deserialize)]
struct LiveMessageReq {
    text: String,
    #[serde(default)]
    images: Vec<serde_json::Value>,
    #[serde(default)]
    provider: Option<String>,
    #[serde(default)]
    session_id: Option<String>,
}

/// POST /live/message — sync 模式发消息：广播 user 事件 → 桥接 /ask/stream
/// 收集全文 → 广播 text/done → 落盘会话。
pub(crate) async fn live_message_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    let req: LiveMessageReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let _ = req.images; // 附件与 /chat 路径一致：接受不消费
    let _ = req.provider;

    // 会话解析：指定 id → 解析；未指定 → 新建（cwd 项目）
    let project = state.proc.current_project();
    let (sid, hash) = match &req.session_id {
        Some(id) if !id.is_empty() => {
            let h = store::project_hash(&project.working_dir);
            // 任意项目桶内解析（resolve 全库查）
            let found = store::resolve_session(&state.store_root, id).ok().flatten();
            match found {
                Some(m) => (m.meta.id, m.project_hash),
                None => (id.clone(), h),
            }
        }
        _ => match store::create_session(&state.store_root, &project.working_dir, None) {
            Ok(d) => (d.id, store::project_hash(&d.working_dir)),
            Err(e) => return store_err(e),
        },
    };
    let _ = store::append_message(&state.store_root, &hash, &sid, "user", &req.text, None);
    state.proc.active_chats.lock().unwrap().insert(sid.clone(), now_secs());
    state.proc.publish(json!({"type": "user", "text": req.text, "session_id": sid}));

    // 桥接 /ask/stream（与 chat_api 同协议），收集全文
    let ask = json!({"question": req.text});
    let mut builder = state
        .client
        .post(format!("{}/ask/stream", state.lingclaude_base))
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }
    match builder.json(&ask).send().await {
        Ok(resp) if resp.status().is_success() => {
            let full = resp.text().await.unwrap_or_default();
            let mut answer = String::new();
            for block in full.split("\n\n") {
                if let Some(line) = block.lines().find(|l| l.starts_with("data:")) {
                    let data = line[5..].trim();
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(data) {
                        if v["type"] == "text" {
                            if let Some(c) = v["content"].as_str() {
                                answer.push_str(c);
                                state.proc.publish(json!({"type": "text", "content": c, "session_id": sid}));
                            }
                        }
                    }
                }
            }
            let _ = store::append_message(&state.store_root, &hash, &sid, "assistant", &answer, None);
            state.proc.active_chats.lock().unwrap().remove(&sid);
            state.proc.publish(json!({"type": "done", "session_id": sid}));
            ok_json(json!({"ok": true, "session_id": sid}))
        }
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            state.proc.active_chats.lock().unwrap().remove(&sid);
            state.proc.publish(json!({"type": "error", "message": format!("engine HTTP {status}")}));
            err_msg(StatusCode::BAD_GATEWAY, format!("engine HTTP {status}: {text}"))
        }
        Err(e) => {
            state.proc.active_chats.lock().unwrap().remove(&sid);
            state.proc.publish(json!({"type": "error", "message": format!("engine unreachable: {e}")}));
            err_msg(StatusCode::BAD_GATEWAY, format!("engine unreachable: {e}"))
        }
    }
}

/// POST /live/stop — 引擎 /sessions/:id/stop 桥接（同 /chat/stop 语义）。
pub(crate) async fn live_stop_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct StopReq {
        #[serde(default)]
        session_id: Option<String>,
    }
    let req: StopReq = match serde_json::from_slice(&body) {
        Ok(r) => r,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let Some(sid) = req.session_id.filter(|s| !s.is_empty()) else {
        return err_msg(StatusCode::BAD_REQUEST, "session_id is required");
    };
    let mut builder = state
        .client
        .post(format!("{}/sessions/{sid}/stop", state.lingclaude_base))
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }
    match builder.send().await {
        Ok(resp) if resp.status().is_success() => {
            state.proc.active_chats.lock().unwrap().remove(&sid);
            state.proc.publish(json!({"type": "stopped", "session_id": sid}));
            ok_json(json!({"ok": true, "stopped": true}))
        }
        Ok(resp) if resp.status() == StatusCode::NOT_FOUND => ok_json(json!({
            "ok": true, "stopped": false,
            "reason": "engine has no session with this id",
        })),
        Ok(resp) => err_msg(
            StatusCode::BAD_GATEWAY,
            format!("engine HTTP {}", resp.status()),
        ),
        Err(e) => err_msg(StatusCode::BAD_GATEWAY, format!("engine unreachable: {e}")),
    }
}

/// POST /live/permission — 引擎 /permission 桥接（同 /chat/permission）。
pub(crate) async fn live_permission_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    bridge_post_json(state, "/permission", &body).await
}

/// POST /live/user-input — 引擎无交互问答通道：诚实拒绝。
pub(crate) async fn live_user_input_post() -> Response {
    ok_json(json!({
        "accepted": false,
        "reason": "engine bridge has no interactive user-input channel",
    }))
}

/// POST /live/provider — 模型切换：lc 引擎配置单模型，接受并回显。
pub(crate) async fn live_provider_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct ProviderReq {
        provider: String,
    }
    let Ok(req) = serde_json::from_slice::<ProviderReq>(&body) else {
        return err_msg(StatusCode::BAD_REQUEST, "bad request: provider required");
    };
    state.proc.publish(json!({"type": "provider", "provider": req.provider}));
    ok_json(json!({"ok": true, "provider": req.provider}))
}

/// POST /live/compact — 引擎 /ask 通道无显式压缩端点：诚实拒绝（契约内）。
pub(crate) async fn live_compact_post() -> Response {
    ok_json(json!({
        "ok": false,
        "error": "engine bridge does not expose compact; engine auto-compacts by config",
    }))
}

/// POST /live/reasoning_effort — lc 模型目录无 effort 语义：接受并回显。
pub(crate) async fn live_reasoning_effort_post(body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct EffortReq {
        #[serde(default)]
        reasoning_effort: Option<String>,
        #[serde(default)]
        provider: Option<String>,
    }
    let Ok(req) = serde_json::from_slice::<EffortReq>(&body) else {
        return err_msg(StatusCode::BAD_REQUEST, "bad request");
    };
    ok_json(json!({
        "ok": true,
        "reasoning_effort": req.reasoning_effort,
        "provider": req.provider,
    }))
}

/// POST /live/switch_session — 广播切换事件（跨标签页跟随）。
pub(crate) async fn live_switch_session_post(State(state): State<AppState>, body: axum::body::Bytes) -> Response {
    #[derive(Deserialize)]
    struct SwitchReq {
        session_id: String,
        #[serde(default)]
        project_hash: Option<String>,
    }
    let Ok(req) = serde_json::from_slice::<SwitchReq>(&body) else {
        return err_msg(StatusCode::BAD_REQUEST, "bad request: session_id required");
    };
    state.proc.publish(json!({
        "type": "switch_session",
        "session_id": req.session_id,
        "project_hash": req.project_hash,
    }));
    ok_json(json!({"ok": true, "session_id": req.session_id}))
}

/// POST /live/mcp/trust — MCP 状态恒信任（见 /mcp/status）。
pub(crate) async fn live_mcp_trust_post() -> Response {
    ok_json(json!({"ok": true, "trusted": true}))
}

// ─── 桥接助手 ────────────────────────────────────────────────────────────

async fn bridge_get_json(state: AppState, path: &str) -> Response {
    let mut builder = state.client.get(format!("{}{path}", state.lingclaude_base));
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }
    match builder.send().await {
        Ok(resp) if resp.status().is_success() => match resp.json::<serde_json::Value>().await {
            Ok(v) => ok_json(v),
            Err(e) => err_msg(StatusCode::BAD_GATEWAY, format!("bridge parse failed: {e}")),
        },
        Ok(resp) => err_msg(StatusCode::BAD_GATEWAY, format!("engine HTTP {}", resp.status())),
        Err(e) => err_msg(StatusCode::BAD_GATEWAY, format!("engine unreachable: {e}")),
    }
}

async fn bridge_post_json(state: AppState, path: &str, body: &[u8]) -> Response {
    let payload: serde_json::Value = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(e) => return err_msg(StatusCode::BAD_REQUEST, format!("bad request: {e}")),
    };
    let mut builder = state
        .client
        .post(format!("{}{path}", state.lingclaude_base))
        .header("Content-Type", "application/json");
    if let Some(key) = &state.lingclaude_api_key {
        builder = builder.header("X-API-Key", key);
    }
    match builder.json(&payload).send().await {
        Ok(resp) if resp.status().is_success() => match resp.json::<serde_json::Value>().await {
            Ok(v) => ok_json(v),
            Err(e) => err_msg(StatusCode::BAD_GATEWAY, format!("bridge parse failed: {e}")),
        },
        Ok(resp) => {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            err_msg(StatusCode::BAD_GATEWAY, format!("engine HTTP {status}: {text}"))
        }
        Err(e) => err_msg(StatusCode::BAD_GATEWAY, format!("engine unreachable: {e}")),
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ─── 测试 ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audit::AuditLogger;
    use axum::http::Request;
    use tower::ServiceExt;

    fn test_app() -> axum::Router {
        // 每个测试独立存储目录 — 显式注入（store_root），零环境变量/全局锁
        let store_root = std::env::temp_dir()
            .join(format!("lc-daemon-api-{}", uuid::Uuid::new_v4()));
        let state = AppState {
            tokens: Arc::new(crate::auth::TokenStore::default()),
            audit: Arc::new(AuditLogger::new(std::env::temp_dir().join("lc-daemon-api-test-audit.jsonl"))),
            allowed_hosts: vec![],
            port: 23458,
            enforce_token: false, // 直测 handler 契约，鉴权由 main.rs 测试覆盖
            lingclaude_base: "http://127.0.0.1:1".into(),
            lingclaude_api_key: None,
            spec_path: None,
            proc: Arc::new(ProcessState::new("127.0.0.1".into())),
            store_root,
            client: reqwest::Client::new(),
        };
        crate::build_router(state)
    }

    async fn req_json(app: axum::Router, method: &str, uri: &str, body: &str) -> (StatusCode, serde_json::Value) {
        let builder = Request::builder().method(method).uri(uri);
        let req = if body.is_empty() {
            builder.body(axum::body::Body::empty()).unwrap()
        } else {
            builder
                .header("content-type", "application/json")
                .body(axum::body::Body::from(body.to_string()))
                .unwrap()
        };
        let resp = app.oneshot(req).await.unwrap();
        let status = resp.status();
        let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        let v = if bytes.is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null)
        };
        (status, v)
    }

    #[tokio::test]
    async fn approval_mode_roundtrip_and_validation() {
        let app = test_app();
        let (s, v) = req_json(app.clone(), "GET", "/approval_mode", "").await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        // 非法模式：200 + ok:false（前端 throw，契约内失败）
        let (s, v) = req_json(app.clone(), "POST", "/approval_mode", r#"{"mode":"yolo"}"#).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], false);
        // 合法模式
        let (s, v) = req_json(app.clone(), "POST", "/approval_mode", r#"{"mode":"plan"}"#).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        assert_eq!(v["mode"], "plan");
        let (_, v) = req_json(app, "GET", "/approval_mode", "").await;
        assert_eq!(v["mode"], "plan");
    }

    #[tokio::test]
    async fn session_crud_endpoints_wire_contract() {
        let app = test_app();
        // create（用真实存在的目录）
        let dir = std::env::temp_dir().to_string_lossy().into_owned();
        let (s, v) = req_json(
            app.clone(),
            "POST",
            "/sessions",
            &format!(r#"{{"working_dir":"{dir}","title":"契约测试"}}"#),
        )
        .await;
        assert_eq!(s, StatusCode::OK, "create failed: {v}");
        let id = v["id"].as_str().unwrap().to_string();
        let hash = v["project_hash"].as_str().unwrap().to_string();
        assert!(!hash.is_empty());

        // list recent（SessionMetaWithProject：flatten + project_hash）
        let (s, v) = req_json(app.clone(), "GET", "/sessions", "").await;
        assert_eq!(s, StatusCode::OK);
        assert!(v.as_array().unwrap().iter().any(|m| m["id"] == id && m["project_hash"] == hash));

        // project sessions（裸 SessionMeta[]）
        let (s, v) = req_json(app.clone(), "GET", &format!("/projects/{hash}/sessions"), "").await;
        assert_eq!(s, StatusCode::OK);
        assert!(v.as_array().unwrap().iter().any(|m| m["id"] == id));
        assert!(v.as_array().unwrap().iter().all(|m| m.get("project_hash").is_none()));

        // detail
        let (s, v) = req_json(
            app.clone(),
            "GET",
            &format!("/projects/{hash}/sessions/{id}"),
            "",
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert!(v["messages"].is_array());

        // search
        let (s, v) = req_json(app.clone(), "GET", "/sessions/search?q=契约", "").await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v.as_array().unwrap().len(), 1);

        // resolve：全 id
        let (s, _) = req_json(app.clone(), "GET", &format!("/sessions/resolve/{id}"), "").await;
        assert_eq!(s, StatusCode::OK);
        // resolve：无匹配 → 404
        let (s, _) = req_json(app.clone(), "GET", "/sessions/resolve/deadbeef-0000-4000-8000-000000000000", "").await;
        assert_eq!(s, StatusCode::NOT_FOUND);

        // rename
        let (s, v) = req_json(
            app.clone(),
            "PATCH",
            &format!("/projects/{hash}/sessions/{id}"),
            r#"{"name":"改名"}"#,
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);

        // delete
        let (s, _) = req_json(app.clone(), "DELETE", &format!("/projects/{hash}/sessions/{id}"), "").await;
        assert_eq!(s, StatusCode::OK);
        let (s, _) = req_json(app, "GET", &format!("/projects/{hash}/sessions/{id}"), "").await;
        assert_eq!(s, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn cd_and_project_state_roundtrip() {
        let app = test_app();
        let dir = std::env::temp_dir().to_string_lossy().into_owned();
        let (s, v) = req_json(app.clone(), "POST", "/cd", &format!(r#"{{"path":"{dir}"}}"#)).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["success"], true);
        assert_eq!(v["current_dir"], dir);
        assert!(!v["project_hash"].as_str().unwrap().is_empty());

        let (_, v) = req_json(app.clone(), "GET", "/project", "").await;
        assert_eq!(v["working_dir"], dir);
        assert!(v["recent_dirs"].as_array().unwrap().iter().any(|d| d == &dir));

        // 不存在的目录 → success:false（契约内失败）
        let (s, v) = req_json(app, "POST", "/cd", r#"{"path":"/nonexistent-dir-xyz"}"#).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["success"], false);
    }

    #[tokio::test]
    async fn config_models_never_leak_api_key() {
        let app = test_app();
        let (s, v) = req_json(app.clone(), "GET", "/config", "").await;
        assert_eq!(s, StatusCode::OK);
        let body = serde_json::to_string(&v).unwrap();
        assert!(!body.contains("ZHIPU_API_KEY") || body.contains("has_api_key"));
        // has_api_key 是布尔，不是密钥字符串
        if let Some(providers) = v["providers"].as_array() {
            for p in providers {
                assert!(p["has_api_key"].is_boolean());
                assert!(p.get("api_key").is_none());
            }
        }
        let (s, v) = req_json(app, "GET", "/models", "").await;
        assert_eq!(s, StatusCode::OK);
        assert!(v.as_array().unwrap().iter().all(|m| m["is_default"].is_boolean()));
    }

    #[tokio::test]
    async fn fs_list_and_mkdir() {
        let app = test_app();
        let base = std::env::temp_dir().join(format!("lc-fs-test-{}", std::process::id()));
        std::fs::create_dir_all(base.join("subdir")).unwrap();
        std::fs::write(base.join("file.txt"), "x").unwrap();
        let (s, v) = req_json(
            app.clone(),
            "GET",
            &format!("/fs/list?path={}", urlencode(&base.to_string_lossy())),
            "",
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["dirs"], json!(["subdir"]));
        assert_eq!(v["files"], json!(["file.txt"]));

        let new_dir = base.join("made-by-test");
        let new_dir_str = new_dir.to_string_lossy();
        let (s, v) = req_json(app, "POST", "/fs/mkdir", &format!(r#"{{"path":"{new_dir_str}"}}"#)).await;
        assert_eq!(s, StatusCode::OK);
        assert!(new_dir.is_dir(), "mkdir response: {v}");
    }

    #[tokio::test]
    async fn stubs_return_contract_shapes_not_404() {
        let app = test_app();
        for (method, uri, body) in [
            ("GET", "/mcp/status", ""),
            ("GET", "/skills", ""),
            ("GET", "/tunnel/status", ""),
            ("POST", "/live/user-input", r#"{"answers":[]}"#),
            ("POST", "/live/compact", "{}"),
            ("POST", "/live/reasoning_effort", r#"{"reasoning_effort":"high"}"#),
            ("POST", "/live/mcp/trust", "{}"),
            ("POST", "/live/provider", r#"{"provider":"glm"}"#),
            ("POST", "/command", r#"{"command":"unknown-cmd"}"#),
            ("POST", "/config/reload", ""),
        ] {
            // 注：/permission/mode 为引擎桥接端点（引擎不可达 → 502 fail-closed），
            // 不在本 stub 清单内；其契约由桥接语义保证。
            let (s, v) = req_json(app.clone(), method, uri, body).await;
            assert_eq!(s, StatusCode::OK, "{method} {uri} → {s} {v}");
            assert!(!v.is_null(), "{method} {uri} 返回空体");
        }
    }

    fn urlencode(s: &str) -> String {
        s.bytes()
            .map(|b| match b {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => (b as char).to_string(),
                _ => format!("%{b:02X}"),
            })
            .collect()
    }
}

//! 会话持久化存储 — daemon 化改造的本地会话目录。
//!
//! 模式对齐 atomcode：sessions 按项目 hash 分桶落盘，列表/搜索/详情全部
//! 从目录扫描得出（不依赖引擎进程）。存储自洽闭环：`/chat` done 事件
//! 与 `/live/message` 转发流会把 user/assistant 消息 append 进会话文件。
//!
//! 目录布局：`<root>/<project_hash>/<session_id>.json`
//! 文件格式：`{ "id", "name", "working_dir", "created_at", "updated_at",
//!             "messages": [ {role, content, ts, ...} ] }`
//!
//! project_hash 仅在 lc 体系内自洽使用（前端拿 hash roundtrip 回本服务），
//! 不与 atomcode 的 hash 算法对齐 — 算法：SHA-256(abs_path) 前 16 hex。

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// 会话元数据 — wire 形状对齐前端 `SessionMeta`（api.ts，秒级时间戳）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SessionSummary {
    pub id: String,
    pub name: String,
    pub working_dir: String,
    pub created_at: u64,
    pub updated_at: u64,
    pub message_count: usize,
    #[serde(default)]
    pub file_size: u64,
}

/// 跨项目列表条目 — `SessionMetaWithProject`（flatten + project_hash）。
#[derive(Debug, Clone, Serialize)]
pub(crate) struct SessionMetaWithProject {
    pub project_hash: String,
    #[serde(flatten)]
    pub meta: SessionSummary,
}

/// 项目信息 — wire 形状对齐前端 `ProjectInfo`。
#[derive(Debug, Clone, Serialize)]
pub(crate) struct ProjectInfo {
    pub hash: String,
    pub name: String,
    pub working_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub session_count: usize,
    pub created_at: u64,
    pub last_updated: u64,
}

/// 会话详情 — 前端 `SessionDetail`（meta 字段 + messages）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct SessionDetail {
    pub id: String,
    pub name: String,
    pub working_dir: String,
    pub created_at: u64,
    pub updated_at: u64,
    pub message_count: usize,
    pub messages: Vec<serde_json::Value>,
}

/// 单条会话文件的反序列化内部形状（messages 宽容：任意 JSON 对象数组）。
#[derive(Debug, Deserialize)]
struct SessionFile {
    id: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    working_dir: String,
    #[serde(default)]
    created_at: u64,
    #[serde(default)]
    updated_at: u64,
    #[serde(default)]
    messages: Vec<serde_json::Value>,
}

/// project_hash — SHA-256(规范化绝对路径) 前 16 hex。lc 体系内自洽。
pub(crate) fn project_hash(path: &str) -> String {
    let abs = std::fs::canonicalize(path)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string());
    let digest = Sha256::digest(abs.as_bytes());
    digest
        .iter()
        .map(|b| format!("{:02x}", b))
        .collect::<String>()
        .chars()
        .take(16)
        .collect()
}

/// 存储根目录（默认 `~/.lingclaude/webui-sessions`；LINGCLAUDE_WEBUI_SESSIONS_DIR
/// 可覆盖）。只在 daemon_api::store_root() 单点调用 — handler 经 AppState 携带
/// 显式 root，测试零环境变量竞态。
pub(crate) fn sessions_root() -> PathBuf {
    if let Ok(dir) = std::env::var("LINGCLAUDE_WEBUI_SESSIONS_DIR") {
        return PathBuf::from(dir);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".lingclaude").join("webui-sessions")
}

/// 互斥写锁 — 同一会话并发 append 串行化（进程内单实例语义）。
static WRITE_LOCK: Mutex<()> = Mutex::new(());

fn session_path(root: &Path, hash: &str, id: &str) -> PathBuf {
    root.join(sanitize(hash)).join(format!("{}.json", sanitize(id)))
}

/// 路径段消毒 — 只允许 [A-Za-z0-9._-]，防目录穿越（id/hash 来自 URL）。
fn sanitize(seg: &str) -> String {
    let cleaned: String = seg
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        .collect();
    if cleaned.is_empty() || cleaned.starts_with('.') || cleaned == ".." {
        "_invalid".into()
    } else {
        cleaned
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn read_session_file(path: &Path) -> Option<SessionFile> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// 新建会话 — 返回完整详情（含初始元数据）。
pub(crate) fn create_session(
    root: &Path,
    working_dir: &str,
    title: Option<String>,
) -> Result<SessionDetail, String> {
    let id = uuid::Uuid::new_v4().to_string();
    let hash = project_hash(working_dir);
    let now = now_secs();
    let detail = SessionDetail {
        id: id.clone(),
        name: title.unwrap_or_else(|| format!("会话 {}", now)),
        working_dir: working_dir.to_string(),
        created_at: now,
        updated_at: now,
        message_count: 0,
        messages: Vec::new(),
    };
    write_session(root, &hash, &detail)?;
    Ok(detail)
}

/// 落盘单条会话（整文件重写 — 会话文件小，简单正确优先）。
fn write_session(root: &Path, hash: &str, detail: &SessionDetail) -> Result<(), String> {
    let _guard = WRITE_LOCK.lock().map_err(|e| e.to_string())?;
    let dir = root.join(sanitize(hash));
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = session_path(root, hash, &detail.id);
    let json = serde_json::to_string_pretty(detail).map_err(|e| e.to_string())?;
    // 原子写：tmp + rename，防中断留下半截 JSON。
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, json).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
    Ok(())
}

/// append 消息 — 不存在则忽略（会话生命周期由 /chat done 事件驱动，竞态无害）。
pub(crate) fn append_message(
    root: &Path,
    hash: &str,
    session_id: &str,
    role: &str,
    content: &str,
    extra: Option<serde_json::Value>,
) -> Result<(), String> {
    let _guard = WRITE_LOCK.lock().map_err(|e| e.to_string())?;
    let path = session_path(root, hash, session_id);
    let Some(mut file) = read_session_file(&path) else {
        return Ok(()); // 会话未由本服务创建（如引擎侧会话）— 不造数据
    };
    let mut msg = serde_json::json!({
        "role": role,
        "content": content,
        "ts": now_secs() * 1000, // 前端 SessionMessage.ts 为 epoch ms
    });
    if let Some(serde_json::Value::Object(map)) = extra {
        if let serde_json::Value::Object(base) = &mut msg {
            for (k, v) in map {
                base.insert(k, v);
            }
        }
    }
    file.messages.push(msg);
    file.updated_at = now_secs();
    let detail = SessionDetail {
        id: file.id,
        name: if file.name.is_empty() { "未命名".into() } else { file.name },
        working_dir: file.working_dir,
        created_at: file.created_at,
        updated_at: file.updated_at,
        message_count: file.messages.len(),
        messages: file.messages,
    };
    write_session(root, hash, &detail)
}

fn scan_bucket(bucket: &Path, hash: &str, out: &mut Vec<(SessionSummary, String)>) {
    let Ok(entries) = std::fs::read_dir(bucket) else { return };
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let Some(file) = read_session_file(&p) else { continue };
        let file_size = entry.metadata().map(|m| m.len()).unwrap_or(0);
        out.push((
            SessionSummary {
                id: file.id,
                name: file.name,
                working_dir: file.working_dir,
                created_at: file.created_at,
                updated_at: file.updated_at,
                message_count: file.messages.len(),
                file_size,
            },
            hash.to_string(),
        ));
    }
}

fn scan_all(root: &Path) -> Result<Vec<(SessionSummary, String)>, String> {
    let mut out = Vec::new();
    let Ok(buckets) = std::fs::read_dir(&root) else {
        return Ok(out); // 目录不存在 = 空列表（首次启动语义）
    };
    for bucket in buckets.flatten() {
        if !bucket.path().is_dir() {
            continue;
        }
        let hash = bucket.file_name().to_string_lossy().into_owned();
        scan_bucket(&bucket.path(), &hash, &mut out);
    }
    Ok(out)
}

/// GET /sessions — 最近 50 条（updated_at 降序），形状 `SessionMetaWithProject[]`。
pub(crate) fn list_recent_sessions(root: &Path) -> Result<Vec<SessionMetaWithProject>, String> {
    let mut all = scan_all(root)?;
    all.sort_by(|a, b| b.0.updated_at.cmp(&a.0.updated_at));
    Ok(all
        .into_iter()
        .take(50)
        .map(|(meta, project_hash)| SessionMetaWithProject { project_hash, meta })
        .collect())
}

/// GET /projects/:hash/sessions — 单桶全量（未封顶），返回裸 `SessionMeta[]`。
pub(crate) fn list_project_sessions(root: &Path, hash: &str) -> Result<Vec<SessionSummary>, String> {
    let mut out = Vec::new();
    scan_bucket(&root.join(sanitize(hash)), hash, &mut out);
    out.sort_by(|a, b| b.0.updated_at.cmp(&a.0.updated_at));
    Ok(out.into_iter().map(|(meta, _)| meta).collect())
}

/// GET /sessions/search?q= — 名称跨项目模糊匹配（大小写不敏感）。
pub(crate) fn search_sessions(root: &Path, q: &str) -> Result<Vec<SessionMetaWithProject>, String> {
    let needle = q.to_lowercase();
    let mut all = scan_all(root)?;
    all.sort_by(|a, b| b.0.updated_at.cmp(&a.0.updated_at));
    Ok(all
        .into_iter()
        .filter(|(m, _)| m.name.to_lowercase().contains(&needle) || m.id.starts_with(q))
        .map(|(meta, project_hash)| SessionMetaWithProject { project_hash, meta })
        .collect())
}

/// GET /sessions/resolve/:id — 短 ID 前缀解析，无匹配 None / 多匹配取最近。
pub(crate) fn resolve_session(root: &Path, id: &str) -> Result<Option<SessionMetaWithProject>, String> {
    let all = scan_all(root)?;
    let matches: Vec<_> = all.iter().filter(|(m, _)| m.id.starts_with(id)).collect();
    Ok(matches
        .into_iter()
        .max_by_key(|(m, _)| m.updated_at)
        .map(|(meta, project_hash)| SessionMetaWithProject {
            project_hash: project_hash.clone(),
            meta: meta.clone(),
        }))
}

/// GET /projects/:hash/sessions/:id — 会话详情（含 messages）。
pub(crate) fn get_session_detail(root: &Path, hash: &str, id: &str) -> Option<SessionDetail> {
    let file = read_session_file(&session_path(root, hash, id))?;
    Some(SessionDetail {
        id: file.id,
        name: if file.name.is_empty() { "未命名".into() } else { file.name },
        working_dir: file.working_dir,
        created_at: file.created_at,
        updated_at: file.updated_at,
        message_count: file.messages.len(),
        messages: file.messages,
    })
}

/// DELETE /projects/:hash/sessions/:id — 删除会话文件。
pub(crate) fn delete_session(root: &Path, hash: &str, id: &str) -> Result<bool, String> {
    let path = session_path(root, hash, id);
    if path.exists() {
        std::fs::remove_file(&path).map_err(|e| e.to_string())?;
        Ok(true)
    } else {
        Ok(false)
    }
}

/// PATCH /projects/:hash/sessions/:id — 重命名（`{name}`）。
pub(crate) fn rename_session(root: &Path, hash: &str, id: &str, name: &str) -> Result<bool, String> {
    let path = session_path(root, hash, id);
    let Some(mut file) = read_session_file(&path) else {
        return Ok(false);
    };
    file.name = name.to_string();
    let detail = SessionDetail {
        id: file.id,
        name: file.name,
        working_dir: file.working_dir,
        created_at: file.created_at,
        updated_at: file.updated_at,
        message_count: file.messages.len(),
        messages: file.messages,
    };
    write_session(root, hash, &detail)?;
    Ok(true)
}

/// GET /projects — 项目聚合（扫描会话目录按桶分组，last_updated 降序）。
pub(crate) fn list_projects(root: &Path) -> Result<Vec<ProjectInfo>, String> {
    let mut by_hash: BTreeMap<String, ProjectInfo> = BTreeMap::new();
    for (meta, hash) in scan_all(root)? {
        let entry = by_hash.entry(hash.clone()).or_insert_with(|| ProjectInfo {
            hash: hash.clone(),
            name: Path::new(&meta.working_dir)
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_else(|| "unknown".into()),
            working_dir: meta.working_dir.clone(),
            description: None,
            session_count: 0,
            created_at: meta.created_at,
            last_updated: meta.updated_at,
        });
        entry.session_count += 1;
        entry.created_at = entry.created_at.min(meta.created_at);
        entry.last_updated = entry.last_updated.max(meta.updated_at);
    }
    let mut projects: Vec<ProjectInfo> = by_hash.into_values().collect();
    projects.sort_by(|a, b| b.last_updated.cmp(&a.last_updated));
    Ok(projects)
}

/// DELETE /projects/:hash — 删除项目桶（含全部会话）。
pub(crate) fn delete_project(root: &Path, hash: &str) -> Result<bool, String> {
    let dir = root.join(sanitize(hash));
    if dir.exists() {
        std::fs::remove_dir_all(&dir).map_err(|e| e.to_string())?;
        Ok(true)
    } else {
        Ok(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_root(tag: &str) -> PathBuf {
        // 每次调用独立子目录 — 无共享状态、无 env、无锁，天然并行安全
        let dir = std::env::temp_dir()
            .join(format!("lc-store-{}-{}", tag, uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn sanitize_blocks_traversal() {
        assert_eq!(sanitize("../../etc"), "_invalid");
        assert_eq!(sanitize(".."), "_invalid");
        assert_eq!(sanitize("abc123-x_y"), "abc123-x_y");
    }

    #[test]
    fn project_hash_is_stable_16hex() {
        let h1 = project_hash("/tmp");
        let h2 = project_hash("/tmp");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 16);
        assert!(h1.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn create_list_append_roundtrip() {
        let root = test_root("roundtrip");
        let dir = "/tmp";
        let detail = create_session(&root, dir, Some("测试会话".into())).expect("create");
        let hash = project_hash(dir);

        append_message(&root, &hash, "nonexistent", "user", "hi", None).unwrap();
        append_message(&root, &hash, &detail.id, "user", "你好", None).unwrap();
        append_message(&root, &hash, &detail.id, "assistant", "你好！有什么可以帮您？", None).unwrap();

        let got = get_session_detail(&root, &hash, &detail.id).expect("detail");
        assert_eq!(got.message_count, 2);
        assert_eq!(got.messages[0]["role"], "user");
        assert_eq!(got.messages[1]["content"], "你好！有什么可以帮您？");

        let recent = list_recent_sessions(&root).unwrap();
        assert!(recent.iter().any(|s| s.meta.id == detail.id));
        assert!(recent.iter().all(|s| !s.project_hash.is_empty()));

        let per_project = list_project_sessions(&root, &hash).unwrap();
        assert_eq!(per_project.len(), 1);

        let found = search_sessions(&root, "测试").unwrap();
        assert_eq!(found.len(), 1);

        let resolved = resolve_session(&root, &detail.id[..8]).unwrap();
        assert!(resolved.is_some());

        assert!(rename_session(&root, &hash, &detail.id, "改名了").unwrap());
        assert_eq!(get_session_detail(&root, &hash, &detail.id).unwrap().name, "改名了");
        assert!(delete_session(&root, &hash, &detail.id).unwrap());
        assert!(get_session_detail(&root, &hash, &detail.id).is_none());
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn projects_aggregate_and_delete() {
        let root = test_root("projects");
        let a = create_session(&root, "/tmp", None).unwrap();
        let b = create_session(&root, "/home", None).unwrap();
        let ha = project_hash("/tmp");
        let hb = project_hash("/home");

        let projects = list_projects(&root).unwrap();
        assert_eq!(projects.len(), 2);
        assert!(projects.iter().all(|p| p.session_count == 1));

        assert!(delete_project(&root, &ha).unwrap());
        assert!(get_session_detail(&root, &ha, &a.id).is_none());
        assert!(get_session_detail(&root, &hb, &b.id).is_some());
        std::fs::remove_dir_all(&root).ok();
    }
}

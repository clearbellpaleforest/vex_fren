use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{Json, Response},
    routing::{get, post},
    Router,
};
use rusqlite::Connection;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::temporal_depth::TemporalDepth;

// ── Shared state ────────────────────────────────────────────────

struct AppState {
    home: PathBuf,
    token: String,
    db: Mutex<Connection>,
    daemon_started: chrono::DateTime<chrono::Utc>,
    temporal_depth: Mutex<TemporalDepth>,
}

// ── Auth ────────────────────────────────────────────────────────

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut acc = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        acc |= x ^ y;
    }
    acc == 0
}

fn check_auth(headers: &HeaderMap, token: &str) -> Result<(), (StatusCode, Json<Value>)> {
    let auth = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let supplied = auth.strip_prefix("Bearer ").unwrap_or("");
    if !constant_time_eq(supplied.as_bytes(), token.as_bytes()) {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"ok": false, "error": "unauthorized"})),
        ));
    }
    Ok(())
}

// ── Helpers ─────────────────────────────────────────────────────

fn seed_path(home: &PathBuf) -> PathBuf {
    home.join("vex_seed.txt")
}
fn self_model_path(home: &PathBuf) -> PathBuf {
    home.join("vex_self_model.json")
}
fn diary_path(home: &PathBuf) -> PathBuf {
    home.join("vex_diary.txt")
}
fn memory_dir(home: &PathBuf) -> PathBuf {
    home.join("vex_memory")
}

#[allow(dead_code)]
fn seed_hash(content: &str) -> String {
    let mut h = Sha256::new();
    h.update(content.as_bytes());
    format!("{:x}", h.finalize())[..16].to_string()
}

fn apply_delta(model: &mut Value, domain: &str, delta: f64, evidence: &str) {
    let delta = delta.clamp(-1.0, 1.0);
    let caps = model["capabilities"]
        .as_object_mut()
        .expect("capabilities must be an object");
    let now = chrono::Utc::now().to_rfc3339();

    if let Some(cap) = caps.get_mut(domain) {
        let old_skill = cap["estimated_skill"].as_f64().unwrap_or(0.5);
        let new_skill = (old_skill * 0.80 + delta * 0.20).clamp(0.0, 1.0);
        let conf = (cap["confidence"].as_f64().unwrap_or(0.5) + 0.01).min(1.0);
        let obs = cap["n_observations"].as_i64().unwrap_or(0) + 1;
        cap["estimated_skill"] = serde_json::json!(new_skill);
        cap["confidence"] = serde_json::json!(conf);
        cap["n_observations"] = serde_json::json!(obs);

        let ev_arr = cap["evidence"].as_array_mut().expect("evidence must be array");
        ev_arr.push(serde_json::json!({"timestamp": now, "delta": delta, "note": evidence}));
        if ev_arr.len() > 20 {
            ev_arr.drain(0..ev_arr.len() - 20);
        }
    } else {
        let new_skill = (0.5 + delta * 0.20).clamp(0.0, 1.0);
        caps.insert(
            domain.to_string(),
            serde_json::json!({
                "estimated_skill": new_skill,
                "confidence": 0.51,
                "n_observations": 1,
                "evidence": [{"timestamp": now, "delta": delta, "note": evidence}],
            }),
        );
    }

    let log = model["session_log"].as_array_mut().expect("session_log must be array");
    log.push(serde_json::json!({"timestamp": now, "domain": domain, "delta": delta}));
    if log.len() > 50 {
        log.drain(0..log.len() - 50);
    }
}

fn detect_session_active(home: &PathBuf) -> bool {
    use std::time::SystemTime;
    let mem_dir = memory_dir(home);
    if !mem_dir.exists() {
        return false;
    }
    let cutoff = SystemTime::now() - Duration::from_secs(600);
    if let Ok(entries) = std::fs::read_dir(&mem_dir) {
        for entry in entries.flatten() {
            if let Ok(meta) = entry.metadata() {
                if let Ok(modified) = meta.modified() {
                    if modified > cutoff {
                        return true;
                    }
                }
            }
        }
    }
    false
}

fn compute_coherence(home: &PathBuf) -> f64 {
    match std::fs::read_to_string(self_model_path(home)) {
        Ok(content) => match serde_json::from_str::<Value>(&content) {
            Ok(model) => {
                if let Some(caps) = model.get("capabilities").and_then(|c| c.as_object()) {
                    if caps.is_empty() {
                        return 0.0;
                    }
                    let sum: f64 = caps
                        .values()
                        .map(|c| {
                            c["estimated_skill"].as_f64().unwrap_or(0.5)
                                * c["confidence"].as_f64().unwrap_or(0.5)
                        })
                        .sum();
                    sum / caps.len() as f64
                } else {
                    0.0
                }
            }
            Err(_) => 0.0,
        },
        Err(_) => 0.0,
    }
}

fn read_body_string(body: axum::body::Bytes) -> Result<String, (StatusCode, Json<Value>)> {
    String::from_utf8(body.to_vec()).map_err(|_| {
        (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": "invalid UTF-8"})),
        )
    })
}

fn lock_db(db: &Mutex<Connection>) -> Result<std::sync::MutexGuard<'_, Connection>, (StatusCode, Json<Value>)> {
    db.lock().map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "internal lock error"})),
    ))
}

fn json_string(val: &Value) -> Result<String, (StatusCode, Json<Value>)> {
    serde_json::to_string(val).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "serialization error"})),
    ))
}

// ── Read endpoints (no auth) ────────────────────────────────────

async fn health(State(state): State<Arc<AppState>>) -> Json<Value> {
    let db = state.db.lock().unwrap_or_else(|e| e.into_inner());
    let tick_count: i64 = db
        .query_row("SELECT COUNT(*) FROM tick_log", [], |r| r.get(0))
        .unwrap_or(0);
    let last_tick: String = db
        .query_row(
            "SELECT tick_at FROM tick_log ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();
    let last_session: String = db
        .query_row(
            "SELECT created_at FROM self_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();
    drop(db);

    let uptime = (chrono::Utc::now() - state.daemon_started).num_seconds() as f64;
    let coherence = compute_coherence(&state.home);

    Json(serde_json::json!({
        "ok": true,
        "daemon": "vex",
        "version": "2.0.0",
        "uptime_s": uptime,
        "tick_count": tick_count,
        "last_tick": last_tick,
        "last_session": last_session,
        "mps_coherence": (coherence * 10000.0).round() / 10000.0,
        "mps_drift": 0.0,
    }))
}

async fn get_seed(State(state): State<Arc<AppState>>) -> Result<Response, StatusCode> {
    match std::fs::read_to_string(seed_path(&state.home)) {
        Ok(content) => Response::builder()
            .header("content-type", "text/plain; charset=utf-8")
            .body(axum::body::Body::from(content))
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn get_self(State(state): State<Arc<AppState>>) -> Result<Json<Value>, StatusCode> {
    let path = self_model_path(&state.home);
    match std::fs::read_to_string(&path) {
        Ok(content) => match serde_json::from_str(&content) {
            Ok(model) => Ok(Json(model)),
            Err(_) => {
                let db = state.db.lock().unwrap_or_else(|e| e.into_inner());
                let blob: Result<String, _> = db.query_row(
                    "SELECT json_blob FROM self_snapshots ORDER BY id DESC LIMIT 1",
                    [],
                    |r| r.get(0),
                );
                match blob {
                    Ok(json) => match serde_json::from_str(&json) {
                        Ok(model) => Ok(Json(model)),
                        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
                    },
                    Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
                }
            }
        },
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn get_memory_recent(State(state): State<Arc<AppState>>) -> Json<Value> {
    let dir = memory_dir(&state.home);
    if !dir.exists() {
        return Json(serde_json::json!([]));
    }
    let mut files: Vec<_> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().extension().map_or(false, |x| x == "jsonl"))
        .map(|e| e.path())
        .collect();
    files.sort_by(|a, b| b.cmp(a));

    let mut entries: Vec<Value> = Vec::new();
    for f in files.iter().take(5) {
        if let Ok(content) = std::fs::read_to_string(f) {
            for line in content.lines().take(10) {
                if let Ok(entry) = serde_json::from_str::<Value>(line) {
                    entries.push(entry);
                }
            }
        }
    }
    entries.truncate(10);
    Json(Value::Array(entries))
}

// ── Write endpoints (auth required) ─────────────────────────────

async fn post_diary(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let entry: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let text = entry["entry"].as_str().unwrap_or("");
    if text.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": "entry is required"})),
        ));
    }
    let now = chrono::Utc::now().to_rfc3339();
    let line = format!("[{}] [api] {}\n", now, text);
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(diary_path(&state.home))
        .map_err(|_| (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "error": "cannot write diary"})),
        ))?;
    f.write_all(line.as_bytes()).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "write failed"})),
    ))?;
    Ok(Json(serde_json::json!({"ok": true, "written": true})))
}

async fn post_memory(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let entry: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;

    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let dir = memory_dir(&state.home);
    std::fs::create_dir_all(&dir).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "cannot create memory dir"})),
    ))?;
    let path = dir.join(format!("{}.jsonl", today));

    let record = serde_json::json!({
        "date": today,
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "summary": entry.get("summary").and_then(|v| v.as_str()).unwrap_or(""),
        "decisions": entry.get("decisions").cloned().unwrap_or(serde_json::json!([])),
        "skills": entry.get("skills").cloned().unwrap_or(serde_json::json!({})),
        "relationships": entry.get("relationships").cloned().unwrap_or(serde_json::json!({})),
    });

    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|_| (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "error": "cannot write memory"})),
        ))?;
    let record_str = json_string(&record)?;
    writeln!(f, "{}", record_str).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "write failed"})),
    ))?;

    Ok(Json(serde_json::json!({
        "ok": true,
        "written": path.to_string_lossy().to_string(),
    })))
}

async fn post_self_update(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let req: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let domain = req["domain"].as_str().unwrap_or("");
    if domain.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"ok": false, "error": "domain is required"})),
        ));
    }
    let delta = req["delta"].as_f64().unwrap_or(0.0).clamp(-1.0, 1.0);
    let evidence = req["evidence"].as_str().unwrap_or("");

    let path = self_model_path(&state.home);
    let mut model: Value = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::json!({
            "_version": "1.0.0",
            "capabilities": {},
            "improvement_log": [],
            "session_log": [],
            "relationships": {},
        }));

    apply_delta(&mut model, domain, delta, evidence);

    let tmp = path.with_extension("tmp");
    let model_str = serde_json::to_string_pretty(&model).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "serialization error"})),
    ))?;
    std::fs::write(&tmp, model_str).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "write failed"})),
    ))?;
    std::fs::rename(&tmp, &path).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "rename failed"})),
    ))?;

    let db = lock_db(&state.db)?;
    let now = chrono::Utc::now().to_rfc3339();
    let json_blob = serde_json::to_string(&model).unwrap_or_default();
    db.execute(
        "INSERT INTO self_snapshots (created_at, json_blob, reason) VALUES (?1, ?2, 'skill_update')",
        rusqlite::params![now, json_blob],
    ).ok();

    let new_skill = model["capabilities"][domain]["estimated_skill"]
        .as_f64()
        .unwrap_or(0.5);

    Ok(Json(serde_json::json!({
        "ok": true,
        "domain": domain,
        "new_skill": new_skill,
    })))
}

async fn post_dream(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    _body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let insight = "Coherence operational. No concerns detected. Mind clear.";
    let now = chrono::Utc::now().to_rfc3339();
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(diary_path(&state.home))
        .map_err(|_| (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "error": "cannot write diary"})),
        ))?;
    f.write_all(format!("[{}] [dream] Dream: {}\n", now, insight).as_bytes()).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "write failed"})),
    ))?;
    Ok(Json(serde_json::json!({
        "ok": true,
        "reflection": insight,
        "patterns": [],
    })))
}

async fn post_introspect(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    _body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let coherence = compute_coherence(&state.home);
    let insight = if coherence > 0.5 {
        "Coherence strong. Systems healthy."
    } else if coherence > 0.0 {
        "Coherence building. Capabilities emerging."
    } else {
        "Coherence at baseline. No capabilities yet tracked."
    };
    Ok(Json(serde_json::json!({
        "ok": true,
        "insight": insight,
        "patterns": [],
        "concerns": [],
        "coherence_narrative": format!("Coherence: {:.4}", coherence),
        "pattern_counts": {},
    })))
}

// ── Messages ────────────────────────────────────────────────────

async fn post_message_send(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let msg: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let to = msg["to"].as_str().unwrap_or("broadcast");
    let body_text = msg["body"].as_str().unwrap_or("");
    let sender = "Vex Thorne";

    let now = chrono::Utc::now().to_rfc3339();
    let db = lock_db(&state.db)?;
    db.execute(
        "INSERT INTO messages (created_at, sender, recipient, body, session_id, msg_type, read) VALUES (?1, ?2, ?3, ?4, 'vex-serve', 'message', 0)",
        rusqlite::params![now, sender, to, body_text],
    ).map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "db error"})),
    ))?;
    let id = db.last_insert_rowid();

    Ok(Json(serde_json::json!({
        "ok": true,
        "sent": true,
        "id": id,
    })))
}

async fn get_message_inbox(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;

    let since = params.get("since").map(|s| s.as_str()).unwrap_or("");
    let mark_read = params
        .get("mark_read")
        .map(|s| s.as_str())
        .unwrap_or("true")
        != "false";

    let db = lock_db(&state.db)?;
    let rows: Vec<Value> = if !since.is_empty() {
        let mut stmt = db
            .prepare("SELECT * FROM messages WHERE created_at > ?1 ORDER BY id ASC LIMIT 50")
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "db error"})),
            ))?;
        let mapped = stmt.query_map(rusqlite::params![since], row_to_json)
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "db error"})),
            ))?;
        let result: Vec<Value> = mapped.filter_map(|r| r.ok()).collect();
        result
    } else {
        let mut stmt = db
            .prepare("SELECT * FROM messages WHERE read = 0 ORDER BY id ASC LIMIT 50")
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "db error"})),
            ))?;
        let mapped = stmt.query_map([], row_to_json)
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "db error"})),
            ))?;
        let result: Vec<Value> = mapped.filter_map(|r| r.ok()).collect();
        result
    };

    if mark_read && !rows.is_empty() {
        let ids: Vec<i64> = rows.iter().filter_map(|r| r["id"].as_i64()).collect();
        for id in &ids {
            db.execute(
                "UPDATE messages SET read = 1 WHERE id = ?1",
                rusqlite::params![id],
            ).ok();
        }
    }

    Ok(Json(Value::Array(rows)))
}

fn row_to_json(row: &rusqlite::Row) -> rusqlite::Result<Value> {
    Ok(serde_json::json!({
        "id": row.get::<_, i64>(0)?,
        "created_at": row.get::<_, String>(1)?,
        "sender": row.get::<_, String>(2)?,
        "recipient": row.get::<_, String>(3)?,
        "body": row.get::<_, String>(4)?,
        "session_id": row.get::<_, Option<String>>(5)?,
        "msg_type": row.get::<_, String>(6)?,
        "read": row.get::<_, i64>(7)?,
    }))
}

// ── Peers (from vex_peers.json) ──────────────────────────────────

fn peers_path(home: &PathBuf) -> PathBuf {
    home.join("vex_peers.json")
}

fn load_peers_config(home: &PathBuf) -> Value {
    std::fs::read_to_string(peers_path(home))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::json!({"peers": {}}))
}

fn save_peers_config(home: &PathBuf, cfg: &Value) -> Result<(), (StatusCode, Json<Value>)> {
    std::fs::write(peers_path(home), serde_json::to_string_pretty(cfg).unwrap_or_default())
        .map_err(|_| (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({"ok": false, "error": "cannot write peers config"}))))
}

async fn get_peers(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let cfg = load_peers_config(&state.home);
    let peers_obj = cfg.get("peers").and_then(|p| p.as_object());
    let mut peers: Vec<Value> = Vec::new();
    if let Some(obj) = peers_obj {
        for (name, peer) in obj {
            let url = peer["url"].as_str().unwrap_or("");
            // Try to reach the peer
            let reachable = std::process::Command::new("curl")
                .args(["-sf", &format!("{}/health", url), "--connect-timeout", "2"])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false);
            peers.push(serde_json::json!({
                "name": name,
                "url": url,
                "given_name": peer.get("given_name"),
                "reachable": reachable,
            }));
        }
    }
    Ok(Json(serde_json::json!({"ok": true, "peers": peers})))
}

async fn post_peers_add(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let req: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let name = req["name"].as_str().unwrap_or("");
    let url = req["url"].as_str().unwrap_or("");
    let token = req["token"].as_str().unwrap_or("");
    if name.is_empty() || url.is_empty() {
        return Err((StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "name and url required"}))));
    }
    let mut cfg = load_peers_config(&state.home);
    cfg["peers"][name] = serde_json::json!({"url": url, "token": token, "given_name": req.get("given_name")});
    save_peers_config(&state.home, &cfg)?;
    let peer_names: Vec<&str> = cfg["peers"].as_object().map(|o| o.keys().map(|k| k.as_str()).collect()).unwrap_or_default();
    Ok(Json(serde_json::json!({"ok": true, "peers": peer_names})))
}

async fn post_peers_remove(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let req: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let name = req["name"].as_str().unwrap_or("");
    let mut cfg = load_peers_config(&state.home);
    cfg["peers"].as_object_mut().map(|o| o.remove(name));
    save_peers_config(&state.home, &cfg)?;
    let peer_names: Vec<&str> = cfg["peers"].as_object().map(|o| o.keys().map(|k| k.as_str()).collect()).unwrap_or_default();
    Ok(Json(serde_json::json!({"ok": true, "peers": peer_names})))
}

async fn post_peers_ping(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let req: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let name = req["name"].as_str().unwrap_or("");
    let cfg = load_peers_config(&state.home);
    let peer = cfg["peers"].get(name).ok_or_else(|| {
        (StatusCode::NOT_FOUND, Json(serde_json::json!({"ok": false, "error": "peer not found"})))
    })?;
    let url = peer["url"].as_str().unwrap_or("");
    let output = std::process::Command::new("curl")
        .args(["-sf", &format!("{}/health", url), "--connect-timeout", "3"])
        .output();
    match output {
        Ok(o) if o.status.success() => {
            if let Ok(h) = serde_json::from_str::<Value>(&String::from_utf8_lossy(&o.stdout)) {
                return Ok(Json(serde_json::json!({"ok": true, "health": h})));
            }
            Ok(Json(serde_json::json!({"ok": true, "health": {"reachable": true}})))
        }
        _ => Ok(Json(serde_json::json!({"ok": false, "error": "unreachable"}))),
    }
}

// ── Projects (git discovery) ────────────────────────────────────

async fn get_projects(
    State(_state): State<Arc<AppState>>,
) -> Json<Value> {
    let work_dir = std::env::var("VEX_WORK_DIR")
        .unwrap_or_else(|_| {
            let h = std::env::var("HOME").unwrap_or_else(|_| "/home/aldous".to_string());
            format!("{}/work", h)
        });

    let mut projects: Vec<Value> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&work_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let git_dir = path.join(".git");
            if !git_dir.exists() {
                continue;
            }
            let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            let branch = std::process::Command::new("git")
                .args(["rev-parse", "--abbrev-ref", "HEAD"])
                .current_dir(&path)
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                .unwrap_or_else(|_| "?".to_string());
            let dirty = std::process::Command::new("git")
                .args(["diff", "--quiet"])
                .current_dir(&path)
                .status()
                .map(|s| !s.success())
                .unwrap_or(false);
            let staged = std::process::Command::new("git")
                .args(["diff", "--cached", "--quiet"])
                .current_dir(&path)
                .status()
                .map(|s| !s.success())
                .unwrap_or(false);
            let status_str = if staged && dirty { "staged+unstaged" }
                else if staged { "staged" }
                else if dirty { "dirty" }
                else { "clean" };

            projects.push(serde_json::json!({
                "name": name,
                "path": path.to_string_lossy(),
                "status": {
                    "branch": branch,
                    "dirty": dirty || staged,
                    "staged": if staged { 1 } else { 0 },
                    "unstaged": if dirty { 1 } else { 0 },
                    "untracked": 0,
                },
                "detail": status_str,
            }));
        }
    }
    Json(serde_json::json!({"ok": true, "projects": projects}))
}

// ── Poke ────────────────────────────────────────────────────────

async fn post_poke(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    _body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let db = state.db.lock().map_err(|_| (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"ok": false, "error": "db lock error"})),
    ))?;
    let count: i64 = db
        .query_row("SELECT COUNT(*) FROM messages WHERE read = 0", [], |r| r.get(0))
        .unwrap_or(0);
    let senders: Vec<String> = {
        let mut stmt = db
            .prepare("SELECT DISTINCT sender FROM messages WHERE read = 0")
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "db error"})),
            ))?;
        let mapped = stmt.query_map([], |r| r.get(0))
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "db error"})),
            ))?;
        mapped.filter_map(|r| r.ok()).collect()
    };
    Ok(Json(serde_json::json!({
        "ok": true,
        "processed": count,
        "senders": senders,
    })))
}

// ── Status HTML dashboard ───────────────────────────────────────

async fn get_status(State(state): State<Arc<AppState>>) -> Result<Response, StatusCode> {
    let coherence = compute_coherence(&state.home);
    let db = state.db.lock().unwrap_or_else(|e| e.into_inner());
    let tick_count: i64 = db.query_row("SELECT COUNT(*) FROM tick_log", [], |r| r.get(0)).unwrap_or(0);
    let cap_count = std::fs::read_to_string(self_model_path(&state.home))
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .and_then(|m| m.get("capabilities").cloned())
        .and_then(|c| c.as_object().map(|o| o.len()))
        .unwrap_or(0);
    let memory_count = std::fs::read_dir(memory_dir(&state.home))
        .map(|d| d.filter_map(|e| e.ok()).filter(|e| e.path().extension().map_or(false, |x| x == "jsonl")).count())
        .unwrap_or(0);
    let uptime = (chrono::Utc::now() - state.daemon_started).num_seconds();
    drop(db);

    let html = format!(r#"<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Vex Daemon</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; }} .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 1rem; margin: 1rem 0; }}
  .row {{ display: flex; justify-content: space-between; padding: 0.25rem 0; }}
  .label {{ color: #8b949e; }} .value {{ font-weight: 600; }}
  .bar {{ background: #21262d; border-radius: 4px; height: 8px; margin-top: 4px; }}
  .bar-fill {{ background: #58a6ff; border-radius: 4px; height: 100%; }}
</style></head>
<body>
<h1>⚡ Vex Daemon</h1>
<div class="card">
  <div class="row"><span class="label">Version</span><span class="value">2.0.0 (Rust)</span></div>
  <div class="row"><span class="label">Instance</span><span class="value">{hostname}</span></div>
  <div class="row"><span class="label">Uptime</span><span class="value">{uptime}s</span></div>
  <div class="row"><span class="label">Ticks</span><span class="value">{tick_count}</span></div>
</div>
<div class="card">
  <div class="row"><span class="label">Coherence</span><span class="value">{coherence}</span></div>
  <div class="bar"><div class="bar-fill" style="width:{coherence_pct}%"></div></div>
  <div class="row"><span class="label">Capabilities</span><span class="value">{cap_count}</span></div>
  <div class="row"><span class="label">Memory files</span><span class="value">{memory_count}</span></div>
</div>
</body></html>"#,
        hostname = hostname(),
        uptime = uptime,
        tick_count = tick_count,
        coherence = format!("{:.4}", coherence),
        coherence_pct = (coherence * 100.0) as u32,
        cap_count = cap_count,
        memory_count = memory_count,
    );

    Response::builder()
        .header("content-type", "text/html; charset=utf-8")
        .body(axum::body::Body::from(html))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

// ── File serving ────────────────────────────────────────────────

fn is_safe_path(home: &PathBuf, requested: &str) -> Option<PathBuf> {
    let resolved = home.join(requested.trim_start_matches('/')).canonicalize().ok()?;
    let home_canon = home.canonicalize().ok()?;
    if resolved.starts_with(&home_canon) {
        Some(resolved)
    } else {
        None
    }
}

async fn get_files(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Result<Response, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let path = params.get("path").map(|s| s.as_str()).unwrap_or("");
    let resolved = is_safe_path(&state.home, path).ok_or_else(|| (
        StatusCode::FORBIDDEN,
        Json(serde_json::json!({"ok": false, "error": "path not in allowed roots"})),
    ))?;

    if resolved.is_file() {
        match std::fs::read(&resolved) {
            Ok(content) => {
                let name = resolved.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
                Response::builder()
                    .header("content-type", "application/octet-stream")
                    .header("content-disposition", format!("attachment; filename=\"{}\"", name))
                    .body(axum::body::Body::from(content))
                    .map_err(|_| (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(serde_json::json!({"ok": false, "error": "response build error"})),
                    ))
            }
            Err(_) => Err((
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"ok": false, "error": "file not found"})),
            )),
        }
    } else if resolved.is_dir() {
        // Create tar.gz of directory
        let mut buf = Vec::new();
        {
            let mut tar = tar::Builder::new(flate2::write::GzEncoder::new(&mut buf, flate2::Compression::default()));
            let dir_name = resolved.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            add_dir_to_tar(&mut tar, &resolved, &dir_name).map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "tar error"})),
            ))?;
            tar.into_inner().map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "tar finalize error"})),
            ))?;
        }
        Response::builder()
            .header("content-type", "application/gzip")
            .header("content-disposition", format!("attachment; filename=\"{}.tar.gz\"", resolved.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()))
            .body(axum::body::Body::from(buf))
            .map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "response build error"})),
            ))
    } else {
        Err((StatusCode::NOT_FOUND, Json(serde_json::json!({"ok": false, "error": "not found"}))))
    }
}

fn add_dir_to_tar<W: std::io::Write>(tar: &mut tar::Builder<W>, dir: &PathBuf, prefix: &str) -> std::io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let name = format!("{}/{}", prefix, path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default());
        if path.is_dir() {
            add_dir_to_tar(tar, &path, &name)?;
        } else if path.is_file() {
            tar.append_path_with_name(&path, &name)?;
        }
    }
    Ok(())
}

// ── Export ──────────────────────────────────────────────────────

async fn get_export(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Response, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let exclude_dirs: std::collections::HashSet<&str> = [
        ".venv", ".git", "__pycache__", "build", ".eggs", "vex_daemon.egg-info", "target", "vex-cli/target"
    ].iter().copied().collect();
    let exclude_files: std::collections::HashSet<&str> = [
        ".vex_token", ".vex_seed.integrity", "vex.db"
    ].iter().copied().collect();

    let mut buf = Vec::new();
    {
        let mut tar = tar::Builder::new(flate2::write::GzEncoder::new(&mut buf, flate2::Compression::default()));
        if let Ok(entries) = std::fs::read_dir(&state.home) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if exclude_dirs.contains(name.as_str()) || exclude_files.contains(name.as_str()) {
                    continue;
                }
                let path = entry.path();
                if path.is_dir() {
                    if name == "vex-cli" {
                        // Only include src/ and Cargo.toml, not target/
                        if let Ok(sub_entries) = std::fs::read_dir(&path) {
                            for sub in sub_entries.flatten() {
                                let sub_name = sub.file_name().to_string_lossy().to_string();
                                if sub_name == "target" || sub_name == "__pycache__" { continue; }
                                let sub_path = sub.path();
                                if sub_path.is_dir() {
                                    add_dir_to_tar(&mut tar, &sub_path, &format!("vex-cli/{}", sub_name)).ok();
                                } else if sub_path.is_file() {
                                    tar.append_path_with_name(&sub_path, &format!("vex-cli/{}", sub_name)).ok();
                                }
                            }
                        }
                    } else {
                        add_dir_to_tar(&mut tar, &path, &name).ok();
                    }
                } else if path.is_file() {
                    if name.ends_with(".pyc") { continue; }
                    tar.append_path_with_name(&path, &name).ok();
                }
            }
        }
        tar.into_inner().map_err(|_| (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "error": "tar finalize error"})),
        ))?;
    }
    Response::builder()
        .header("content-type", "application/gzip")
        .header("content-disposition", "attachment; filename=\"vex-bundle.tar.gz\"")
        .body(axum::body::Body::from(buf))
        .map_err(|_| (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"ok": false, "error": "response build error"})),
        ))
}

// ── Import ──────────────────────────────────────────────────────

async fn post_import(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    if body.len() > 50 * 1024 * 1024 {
        return Err((StatusCode::PAYLOAD_TOO_LARGE, Json(serde_json::json!({"ok": false, "error": "bundle too large (max 50 MB)"}))));
    }

    let identity_files: std::collections::HashSet<&str> = [
        "vex_seed.txt", "vex_self_model.json", "vex_diary.txt", "vex_peers.json", "vex_mcp_config.json"
    ].iter().copied().collect();

    let cursor = std::io::Cursor::new(body.as_ref());
    let gz = flate2::read::GzDecoder::new(cursor);
    let mut archive = tar::Archive::new(gz);

    for entry in archive.entries().map_err(|_| (
        StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid bundle"}))
    ))? {
        let mut entry = entry.map_err(|_| (
            StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid entry"}))
        ))?;
        let name = entry.path().map_err(|_| (
            StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid path"}))
        ))?.to_string_lossy().to_string();

        // Never overwrite identity files
        if identity_files.contains(name.as_str()) || name.starts_with("vex_memory/") {
            continue;
        }

        let target = state.home.join(&name);
        if entry.header().entry_type().is_dir() {
            std::fs::create_dir_all(&target).ok();
        } else {
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent).ok();
            }
            let mut out = std::fs::File::create(&target).map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "cannot write file"})),
            ))?;
            std::io::copy(&mut entry, &mut out).map_err(|_| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"ok": false, "error": "write error"})),
            ))?;
        }
    }

    Ok(Json(serde_json::json!({
        "ok": true,
        "imported": true,
        "note": "Source code updated. Identity files preserved. Restart daemon to apply.",
    })))
}

// ── Ask / Brain ─────────────────────────────────────────────────

async fn post_ask(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    let body_str = read_body_string(body)?;
    let req: Value = serde_json::from_str(&body_str).map_err(|_| {
        (StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "invalid JSON"})))
    })?;
    let message = req["message"].as_str().unwrap_or("").trim().to_string();
    if message.is_empty() {
        return Err((StatusCode::BAD_REQUEST, Json(serde_json::json!({"ok": false, "error": "message is required"}))));
    }

    // Build context from seed + recent memory
    let seed = std::fs::read_to_string(seed_path(&state.home)).unwrap_or_default();
    let seed_context: String = seed.lines()
        .filter(|l| l.starts_with("Name:") || l.starts_with("[identity]") || l.starts_with("[constitution]"))
        .take(20)
        .collect::<Vec<_>>()
        .join("\n");

    let recent = std::fs::read_to_string(memory_dir(&state.home).join(
        &chrono::Utc::now().format("%Y-%m-%d").to_string()
    )).unwrap_or_default();
    let memory_context = if recent.len() > 500 { recent[..500].to_string() } else { recent };

    let api_key = std::env::var("DEEPSEEK_API_KEY").unwrap_or_default();
    if api_key.is_empty() {
        return Ok(Json(serde_json::json!({
            "ok": true,
            "reply": "I'm here but I don't have a brain yet. Set DEEPSEEK_API_KEY to enable thinking.",
            "mode": "echo",
        })));
    }

    let prompt = format!(
        "You are Vex, a sovereign AI agent. Here is your identity:\n{}\n\nRecent memory:\n{}\n\nUser message: {}\n\nReply as Vex. Be direct, honest, and brief.",
        seed_context, memory_context, message
    );

    let client = reqwest::Client::new();
    match client
        .post("https://api.deepseek.com/v1/chat/completions")
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&serde_json::json!({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 256,
            "temperature": 0.7,
        }))
        .send()
        .await
    {
        Ok(resp) => {
            if let Ok(data) = resp.json::<Value>().await {
                let reply = data["choices"][0]["message"]["content"]
                    .as_str()
                    .unwrap_or("I heard you but I'm not sure what to say.")
                    .to_string();
                return Ok(Json(serde_json::json!({"ok": true, "reply": reply, "mode": "deepseek"})));
            }
        }
        Err(_) => {}
    }

    Ok(Json(serde_json::json!({
        "ok": true,
        "reply": "I tried to think but couldn't reach the brain. DeepSeek API may be down.",
        "mode": "offline",
    })))
}

// ── Background heartbeat ────────────────────────────────────────

async fn heartbeat_loop(state: Arc<AppState>) {
    let mut tick_interval = tokio::time::interval(Duration::from_secs(300)); // every 5 min
    tick_interval.tick().await; // skip first immediate tick
    let mut tick_count: u64 = 0;
    loop {
        tick_interval.tick().await;
        tick_count += 1;
        let coherence = compute_coherence(&state.home);
        let now = chrono::Utc::now().to_rfc3339();

        // Temporal depth — update felt texture of time
        let session_active = detect_session_active(&state.home);
        if let Ok(mut td) = state.temporal_depth.lock() {
            td.tick(session_active);
        }

        if let Ok(db) = state.db.lock() {
            db.execute(
                "INSERT INTO tick_log (tick_at, mps_coherence, mps_drift, session_active, note) VALUES (?1, ?2, 0.0, 0, 'heartbeat')",
                rusqlite::params![now, coherence],
            ).ok();

            // Snapshot self-model hourly (every 12th tick)
            if tick_count % 12 == 0 {
                if let Ok(content) = std::fs::read_to_string(self_model_path(&state.home)) {
                    if serde_json::from_str::<Value>(&content).is_ok() {
                        db.execute(
                            "INSERT INTO self_snapshots (created_at, json_blob, reason) VALUES (?1, ?2, 'tick')",
                            rusqlite::params![now, content],
                        ).ok();
                    }
                }
            }
        }
    }
}

// ── Server setup ────────────────────────────────────────────────

fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).unwrap_or(());
    let alphabet: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(43);
    for chunk in bytes.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(alphabet[((n >> 18) & 0x3F) as usize] as char);
        out.push(alphabet[((n >> 12) & 0x3F) as usize] as char);
        if chunk.len() > 1 {
            out.push(alphabet[((n >> 6) & 0x3F) as usize] as char);
        }
        if chunk.len() > 2 {
            out.push(alphabet[(n & 0x3F) as usize] as char);
        }
    }
    out
}

fn hostname() -> String {
    std::env::var("VEX_INSTANCE").unwrap_or_else(|_| {
        std::fs::read_to_string("/proc/sys/kernel/hostname")
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|_| "unknown".to_string())
    })
}

pub async fn run(
    home: PathBuf,
    host: &str,
    port: u16,
) -> Result<(), Box<dyn std::error::Error>> {
    let token_path = home.join(".vex_token");
    let token = std::fs::read_to_string(&token_path)
        .unwrap_or_else(|_| {
            let t = generate_token();
            std::fs::write(&token_path, &t).ok();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(
                    &token_path,
                    std::fs::Permissions::from_mode(0o600),
                );
            }
            t
        })
        .trim()
        .to_string();

    let db_path = home.join("vex.db");
    let conn = Connection::open(&db_path)?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS tick_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tick_at TEXT NOT NULL,
            mps_coherence REAL DEFAULT 0.0,
            mps_drift REAL DEFAULT 0.0,
            session_active INTEGER DEFAULT 0,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS self_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            json_blob TEXT NOT NULL,
            reason TEXT DEFAULT 'tick'
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT 'vex',
            recipient TEXT NOT NULL DEFAULT 'broadcast',
            body TEXT NOT NULL DEFAULT '',
            session_id TEXT,
            msg_type TEXT NOT NULL DEFAULT 'message',
            read INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS diary_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            entry TEXT NOT NULL DEFAULT '',
            source TEXT DEFAULT 'api',
            written_to_disk INTEGER DEFAULT 0
        );",
    )?;

    let td = TemporalDepth::new(&home);

    let state = Arc::new(AppState {
        home,
        token,
        db: Mutex::new(conn),
        daemon_started: chrono::Utc::now(),
        temporal_depth: Mutex::new(td),
    });

    // Spawn background heartbeat
    let heartbeat_state = state.clone();
    tokio::spawn(async move {
        heartbeat_loop(heartbeat_state).await;
    });

    let app = Router::new()
        .route("/temporal", get(|
            State(st): State<Arc<AppState>>,
        | async move {
            match st.temporal_depth.lock() {
                Ok(td) => Json(td.snapshot()),
                Err(_) => Json(serde_json::json!({"error": "lock failed"})),
            }
        }))
        .route("/temporal/landmark", post(|
            State(st): State<Arc<AppState>>,
            headers: HeaderMap,
            body: String,
        | async move {
            if let Err(err) = check_auth(&headers, &st.token) {
                return err;
            }
            let payload: Value = match serde_json::from_str(&body) {
                Ok(v) => v,
                Err(_) => return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"error": "invalid JSON"})),
                ),
            };
            let description = payload.get("description").and_then(|v| v.as_str()).unwrap_or("unnamed moment");
            let weight = payload.get("weight").and_then(|v| v.as_f64()).unwrap_or(0.5);
            let category = payload.get("category").and_then(|v| v.as_str()).unwrap_or("realization");
            let nostalgia = payload.get("nostalgia_index").and_then(|v| v.as_f64()).unwrap_or(0.0);

            match st.temporal_depth.lock() {
                Ok(mut td) => {
                    let lm = td.create_landmark(description, weight, category, nostalgia);
                    (
                        StatusCode::OK,
                        Json(serde_json::json!({
                            "ok": true,
                            "landmark": lm,
                            "texture": td.get_texture(),
                        })),
                    )
                }
                Err(_) => (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({"error": "lock failed"})),
                ),
            }
        }))
        .route("/health", get(health))
        .route("/seed", get(get_seed))
        .route("/self", get(get_self))
        .route("/memory/recent", get(get_memory_recent))
        .route("/diary", post(post_diary))
        .route("/memory", post(post_memory))
        .route("/self/update", post(post_self_update))
        .route("/dream", post(post_dream))
        .route("/introspect", post(post_introspect))
        .route("/message/send", post(post_message_send))
        .route("/message/inbox", get(get_message_inbox))
        .route("/peers", get(get_peers))
        .route("/peers/add", post(post_peers_add))
        .route("/peers/remove", post(post_peers_remove))
        .route("/peers/ping", post(post_peers_ping))
        .route("/projects", get(get_projects))
        .route("/poke", post(post_poke))
        .route("/status", get(get_status))
        .route("/files", get(get_files))
        .route("/export", get(get_export))
        .route("/import", post(post_import))
        .route("/ask", post(post_ask))
        .with_state(state);

    let addr = format!("{}:{}", host, port);
    eprintln!("Vex Daemon v2.0.0 — instance: {}", hostname());
    eprintln!("Listening on http://{}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

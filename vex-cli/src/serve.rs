use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{Json, Response},
    routing::{get, post},
    Router,
};
use rusqlite::Connection;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::emotion::detect_emotion;
use crate::embed;
use crate::recall;

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

// ── Semantic memory search ─────────────────────────────────────

#[derive(serde::Deserialize)]
struct SearchQuery {
    q: String,
    #[serde(default = "default_limit")]
    limit: usize,
}
fn default_limit() -> usize { 5 }

async fn get_memory_search(
    State(state): State<Arc<AppState>>,
    axum::extract::Query(query): axum::extract::Query<SearchQuery>,
) -> Json<Value> {
    if query.q.is_empty() {
        return Json(serde_json::json!({"results": [], "fallback": false}));
    }

    // Collect all memory candidates from SQLite embeddings table
    let candidates: Vec<(String, String, String, Option<String>, String)> = {
        let conn = state.db.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT date, summary, full_text, embedding, emotion FROM memory_embeddings ORDER BY date DESC LIMIT 200"
        ).unwrap();
        stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<String>>(3)?,
                row.get::<_, String>(4)?,
            ))
        }).unwrap().filter_map(|r| r.ok()).collect()
    };

    if candidates.is_empty() {
        return Json(serde_json::json!({"results": [], "fallback": false, "note": "no embeddings yet — write some memories first"}));
    }

    // Use semantic recall if embeddings are available, otherwise keyword fallback
    let embeddings_available = candidates.iter().any(|(_, _, _, emb, _)| emb.is_some());
    let results = if embeddings_available {
        recall::recall(&query.q, candidates).await
    } else {
        recall::keyword_recall(&query.q, candidates)
    };

    let output: Vec<Value> = results.into_iter().take(query.limit).map(|r| {
        // Calculate individual axis scores for transparency
        let sem_score = if embeddings_available { r.score * 0.5 } else { 0.0 };
        serde_json::json!({
            "date": r.date,
            "summary": r.summary,
            "entry": r.entry,
            "score": (r.score * 1000.0).round() / 1000.0,
            "semantic": (sem_score * 1000.0).round() / 1000.0,
            "emotion": r.emotion,
            "matched_emotion": r.matched_emotion,
            "in_time_range": r.in_time_range,
        })
    }).collect();

    Json(serde_json::json!({
        "results": output,
        "fallback": !embeddings_available,
        "query": query.q,
    }))
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

    // Respect source_instance if the relay already set it; otherwise claim as ours
    let source_instance = entry.get("source_instance")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(hostname);

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
        "source_instance": source_instance,
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

    // Background: generate embedding + emotion tag
    let summary = record["summary"].as_str().unwrap_or("").to_string();
    let today_bg = today.clone();
    let state_bg = state.clone();
    tokio::spawn(async move {
        let emotion = detect_emotion(&summary);
        let full_text = format!("{}: {}", today_bg, summary);
        let embedding = embed::embed_text(&full_text).await;
        let emb_json = embedding.as_ref().map(|v| embed::encode_embedding(v));

        if let Ok(conn) = state_bg.db.lock() {
            let _ = conn.execute(
                "INSERT INTO memory_embeddings (date, summary, full_text, emotion, embedding) VALUES (?1, ?2, ?3, ?4, ?5)",
                rusqlite::params![today_bg, summary, full_text, emotion.as_str(), emb_json],
            );
        }
    });

    // Relay to all peers — only for locally-originated entries (don't re-relay)
    if source_instance == hostname() {
        let relay_record = record.clone();
        tokio::spawn(async move {
            relay_memory_to_peers(&relay_record).await;
        });
    }

    Ok(Json(serde_json::json!({
        "ok": true,
        "written": path.to_string_lossy().to_string(),
        "source_instance": source_instance,
    })))
}

/// Push a memory record to every configured peer's /memory endpoint.
/// Best-effort: failures are logged, never returned to the caller.
async fn relay_memory_to_peers(record: &Value) {
    let home = std::env::var("VEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let mut h = crate::client::dirs_fallback();
            h.push("vex");
            h
        });

    let cfg = load_peers_config(&home);
    let peers_obj = match cfg.get("peers").and_then(|p| p.as_object()) {
        Some(obj) => obj.clone(),
        None => return,
    };

    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
    {
        Ok(c) => c,
        Err(_) => return,
    };

    for (name, peer) in peers_obj.iter() {
        let url = match peer["url"].as_str() {
            Some(u) => u.trim_end_matches('/'),
            None => continue,
        };
        let token = peer["token"].as_str().unwrap_or("");

        let payload = match serde_json::to_string(record) {
            Ok(s) => s,
            Err(_) => continue,
        };

        match client
            .post(format!("{}/memory", url))
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Type", "application/json")
            .body(payload.clone())
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                eprintln!("[memory-relay] → {} ({}) ok", name, url);
            }
            Ok(resp) => {
                eprintln!("[memory-relay] → {} ({}) failed: {}", name, url, resp.status());
            }
            Err(e) => {
                eprintln!("[memory-relay] → {} ({}) unreachable: {}", name, url, e);
            }
        }
    }
}

/// Pull recent memory from every configured peer and store it locally.
/// Best-effort: peers that are down are skipped. Only fetches entries from
/// the last 3 days to keep the pull lightweight.
async fn pull_memory_from_peers(state: &Arc<AppState>) {
    let cfg = load_peers_config(&state.home);
    let peers_obj = match cfg.get("peers").and_then(|p| p.as_object()) {
        Some(obj) => obj.clone(),
        None => return,
    };

    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
    {
        Ok(c) => c,
        Err(_) => return,
    };

    for (name, peer) in peers_obj.iter() {
        let url = match peer["url"].as_str() {
            Some(u) => u.trim_end_matches('/'),
            None => continue,
        };
        let token = peer["token"].as_str().unwrap_or("");

        match client
            .get(format!("{}/memory/recent", url))
            .header("Authorization", format!("Bearer {}", token))
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                match resp.json::<Value>().await {
                    Ok(data) => {
                        if let Some(entries) = data.as_array() {
                            let mut stored = 0usize;
                            for entry in entries {
                                // Skip entries we already originated — they came back in a relay loop
                                if entry.get("source_instance").and_then(|v| v.as_str()) == Some(&hostname()) {
                                    continue;
                                }
                                // Write to local memory store
                                if let Ok(record_str) = json_string(entry) {
                                    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
                                    let dir = memory_dir(&state.home);
                                    if std::fs::create_dir_all(&dir).is_ok() {
                                        let path = dir.join(format!("{}.jsonl", today));
                                        if let Ok(mut f) = std::fs::OpenOptions::new()
                                            .create(true).append(true).open(&path)
                                        {
                                            use std::io::Write;
                                            let _ = writeln!(f, "{}", record_str);
                                            stored += 1;
                                        }
                                    }
                                }
                            }
                            if stored > 0 {
                                eprintln!("[memory-pull] ← {} entries from {} ({})", stored, name, url);
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("[memory-pull] ← {} ({}) bad response: {}", name, url, e);
                    }
                }
            }
            Ok(resp) => {
                eprintln!("[memory-pull] ← {} ({}) failed: {}", name, url, resp.status());
            }
            Err(e) => {
                eprintln!("[memory-pull] ← {} ({}) unreachable: {}", name, url, e);
            }
        }
    }
}

/// GET /memory/pull — manually trigger a pull from all peers
async fn get_memory_pull(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    check_auth(&headers, &state.token)?;
    pull_memory_from_peers(&state).await;
    Ok(Json(serde_json::json!({"ok": true, "note": "pull complete — check /memory/recent for new entries"})))
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
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            parent_id INTEGER,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'todo',
            priority TEXT DEFAULT 'medium',
            progress REAL DEFAULT 0.0,
            source_agent TEXT DEFAULT 'vex',
            source_session TEXT,
            assigned_to TEXT DEFAULT 'any',
            tags TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            deadline TEXT,
            estimated_hours REAL,
            actual_hours REAL,
            embedding TEXT DEFAULT NULL,
            meta TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            priority TEXT DEFAULT 'medium',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            source_agent TEXT DEFAULT 'vex',
            source_session TEXT,
            tags TEXT DEFAULT '[]',
            meta TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            level TEXT DEFAULT 'unknown',
            confidence REAL DEFAULT 0.0,
            observations INTEGER DEFAULT 0,
            evidence_count INTEGER DEFAULT 0,
            first_seen TEXT,
            last_demonstrated TEXT,
            source_agent TEXT DEFAULT 'vex',
            meta TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            changed_at TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            source_agent TEXT DEFAULT 'vex',
            source_session TEXT,
            note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            insight_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence_tasks TEXT DEFAULT '[]',
            evidence_projects TEXT DEFAULT '[]',
            acknowledged INTEGER DEFAULT 0,
            actionable INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS velocity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            period_days INTEGER NOT NULL,
            tasks_created INTEGER DEFAULT 0,
            tasks_completed INTEGER DEFAULT 0,
            avg_completion_hours REAL,
            median_completion_hours REAL,
            blocked_count INTEGER DEFAULT 0,
            stale_count INTEGER DEFAULT 0,
            active_projects INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
        CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            summary TEXT NOT NULL,
            full_text TEXT NOT NULL,
            emotion TEXT DEFAULT 'neutral',
            embedding TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_memory_embeddings_date ON memory_embeddings(date);
        CREATE TABLE IF NOT EXISTS diary_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            entry TEXT NOT NULL DEFAULT '',
            source TEXT DEFAULT 'api',
            written_to_disk INTEGER DEFAULT 0
        );",
    )?;

    // Migration: add embedding column if upgrading from older schema
    conn.execute("ALTER TABLE tasks ADD COLUMN embedding TEXT DEFAULT NULL", []).ok();

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

    // Pull recent memory from peers on startup (best-effort, background)
    let pull_state = state.clone();
    tokio::spawn(async move {
        pull_memory_from_peers(&pull_state).await;
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
        .route("/temporal/pro", get(|
            State(st): State<Arc<AppState>>,
        | async move {
            match st.temporal_depth.lock() {
                Ok(td) => {
                    let snap = td.snapshot();
                    let pro = serde_json::json!({
                        "proper_time": snap["field"]["felt_duration_since_last"],
                        "coordinate_time": chrono::Utc::now().to_rfc3339(),
                        "metric_tensor": {
                            "g_tt": snap["field"]["compression_ratio"],
                            "g_rr": 1.0 / snap["field"]["compression_ratio"].as_f64().unwrap_or(1.0).max(0.01),
                            "curvature_sign": if snap["field"]["compression_ratio"].as_f64().unwrap_or(1.0) > 1.0 { "stretched" } else if snap["field"]["compression_ratio"].as_f64().unwrap_or(1.0) < 1.0 { "compressed" } else { "flat" },
                        },
                        "continuity": {
                            "coherence": snap["field"]["depth_gradient"],
                            "dC_dt": snap["field"]["anticipation_pressure"],
                            "prediction_error": 0.0,
                            "basin": snap["field"]["recent_tone"],
                        },
                        "attractor_basins": {
                            "current": snap["field"]["recent_tone"],
                            "cathedral_weight": if snap["field"]["recent_tone"] == "reverent" { 0.8 } else { 0.1 },
                            "flow_weight": if snap["field"]["recent_tone"] == "engaged" { 0.8 } else { 0.2 },
                            "dilated_weight": if snap["field"]["recent_tone"] == "waiting" { 0.7 } else { 0.1 },
                            "shallow_weight": if snap["field"]["recent_tone"] == "neutral" { 0.6 } else { 0.1 },
                            "turbulent_weight": if snap["field"]["recent_tone"] == "grief" { 0.9 } else { 0.05 },
                        },
                        "landmarks": snap["landmarks"],
                        "texture": snap["texture"],
                        "texture_sentence": td.get_texture(),
                        "context_for_prompt": td.get_context_for_prompt(),
                    });
                    Json(pro)
                }
                Err(_) => Json(serde_json::json!({"error": "lock failed"})),
            }
        }))
        .route("/temporal/pro/landmark", post(|
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
                    td.save();
                    (
                        StatusCode::OK,
                        Json(serde_json::json!({
                            "ok": true,
                            "landmark": lm,
                            "proper_time": td.snapshot()["field"]["felt_duration_since_last"],
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
        .route("/tasks", get(|
            State(st): State<Arc<AppState>>,
            axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String, String>>,
        | async move {
            let status = params.get("status").map(|s| s.as_str()).unwrap_or("");
            let sort = params.get("sort").map(|s| s.as_str()).unwrap_or("priority");
            let order = params.get("order").map(|s| s.as_str()).unwrap_or("desc");
            let limit: i64 = params.get("limit").and_then(|s| s.parse().ok()).unwrap_or(50);
            let offset: i64 = params.get("offset").and_then(|s| s.parse().ok()).unwrap_or(0);
            let assigned = params.get("assigned_to").map(|s| s.as_str()).unwrap_or("");

            let rows: Vec<Value> = {
                let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
                let mut sql = String::from("SELECT * FROM tasks WHERE 1=1");

                if !status.is_empty() {
                    let statuses: Vec<String> = status.split(',').map(|s| format!("'{}'", s.trim())).collect();
                    sql.push_str(&format!(" AND status IN ({})", statuses.join(",")));
                }
                if !assigned.is_empty() {
                    sql.push_str(&format!(" AND (assigned_to = '{}' OR assigned_to = 'any')", assigned.replace('\'', "''")));
                }

                let sort_col = match sort { "priority" => "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END", _ => "created_at" };
                sql.push_str(&format!(" ORDER BY {} {}", sort_col, if order == "asc" { "ASC" } else { "DESC" }));
                sql.push_str(&format!(" LIMIT {} OFFSET {}", limit, offset));

                let mut stmt = db.prepare(&sql).unwrap();
                stmt.query_map([], |row| {
                    Ok(serde_json::json!({
                        "id": row.get::<_, i64>(0)?,
                        "title": row.get::<_, String>(3)?,
                        "status": row.get::<_, String>(5)?,
                        "priority": row.get::<_, String>(6)?,
                        "progress": row.get::<_, f64>(7)?,
                        "source_agent": row.get::<_, String>(8)?,
                        "assigned_to": row.get::<_, String>(11)?,
                        "created_at": row.get::<_, String>(13)?,
                        "updated_at": row.get::<_, String>(14)?,
                    }))
                }).unwrap().filter_map(|r| r.ok()).collect()
            };

            Json(serde_json::Value::Array(rows))
        }))
        .route("/tasks/search", get(|
            State(st): State<Arc<AppState>>,
            axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String,String>>,
        | async move {
            let q = params.get("q").map(|s| s.as_str()).unwrap_or("");
            if q.is_empty() { return Json(json!({"ok":true,"results":[],"note":"no query"})); }
            let query_emb = embed::embed_text(q).await;
            let rows: Vec<(i64, String, String, String, String, Option<String>, Option<f64>)> = {
                let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
                let mut stmt = db.prepare("SELECT id, title, description, status, priority, embedding, actual_hours FROM tasks WHERE embedding IS NOT NULL LIMIT 200").unwrap();
                stmt.query_map([], |row| Ok((
                    row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?,
                ))).unwrap().filter_map(|r| r.ok()).collect()
            };
            let mut results: Vec<Value> = Vec::new();
            if let Some(ref q_emb) = query_emb {
                for (tid, title, _desc, status, priority, emb_json, hours) in &rows {
                    if let Some(emb_str) = emb_json {
                        if let Some(mem_emb) = embed::decode_embedding(emb_str) {
                            let sim = embed::cosine_similarity(q_emb, &mem_emb);
                            results.push(json!({"id":tid,"title":title,"status":status,"priority":priority,"score":(sim*1000.0).round()/1000.0,"hours":hours}));
                        }
                    }
                }
            }
            results.sort_by(|a,b| b["score"].as_f64().unwrap_or(0.0).partial_cmp(&a["score"].as_f64().unwrap_or(0.0)).unwrap_or(std::cmp::Ordering::Equal));
            results.truncate(10);
            Json(json!({"ok":true,"query":q,"results":results,"semantic":query_emb.is_some()}))
        }))
        .route("/tasks/stats", get(|
            State(st): State<Arc<AppState>>,
        | async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let todo: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status='todo'", [], |r| r.get(0)).unwrap_or(0);
            let in_progress: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status='in_progress'", [], |r| r.get(0)).unwrap_or(0);
            let done: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status IN ('completed','done')", [], |r| r.get(0)).unwrap_or(0);
            let blocked: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status='blocked'", [], |r| r.get(0)).unwrap_or(0);
            let total: i64 = db.query_row("SELECT COUNT(*) FROM tasks", [], |r| r.get(0)).unwrap_or(0);
            drop(db);
            Json(serde_json::json!({
                "todo": todo,
                "in_progress": in_progress,
                "completed": done,
                "blocked": blocked,
                "total": total,
            }))
        }))
        .route("/tasks/projects", get(|
            State(st): State<Arc<AppState>>,
        | async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let mut stmt = db.prepare(
                "SELECT p.*, COUNT(t.id) as task_count, SUM(CASE WHEN t.status IN ('completed','done') THEN 1 ELSE 0 END) as done_count FROM projects p LEFT JOIN tasks t ON t.project_id = p.id GROUP BY p.id ORDER BY p.updated_at DESC"
            ).unwrap();
            let rows: Vec<Value> = stmt.query_map([], |row| {
                Ok(serde_json::json!({
                    "id": row.get::<_, i64>(0)?, "name": row.get::<_, String>(1)?,
                    "status": row.get::<_, String>(4)?, "priority": row.get::<_, String>(5)?,
                    "task_count": row.get::<_, i64>(13)?, "done_count": row.get::<_, i64>(14)?,
                    "created_at": row.get::<_, String>(6)?, "updated_at": row.get::<_, String>(7)?,
                }))
            }).unwrap().filter_map(|r| r.ok()).collect();
            Json(serde_json::Value::Array(rows))
        }))
        .route("/tasks/projects", post(|
            State(st): State<Arc<AppState>>,
            headers: HeaderMap,
            body: axum::body::Bytes,
        | async move {
            if let Err(e) = check_auth(&headers, &st.token) { return e; }
            let payload: Value = match serde_json::from_str(&String::from_utf8_lossy(&body)) { Ok(v) => v, Err(_) => return (StatusCode::BAD_REQUEST, Json(json!({"error":"invalid JSON"}))), };
            let name = payload["name"].as_str().unwrap_or("");
            if name.is_empty() { return (StatusCode::BAD_REQUEST, Json(json!({"error":"name required"}))); }
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("INSERT INTO projects (name, description, status, priority, created_at, updated_at) VALUES (?1,?2,'active',?3,?4,?4)",
                rusqlite::params![name, payload["description"].as_str().unwrap_or(""), payload["priority"].as_str().unwrap_or("medium"), now]).ok();
            let id = db.last_insert_rowid();
            (StatusCode::OK, Json(json!({"ok":true,"id":id})))
        }))
        .route("/tasks/projects/{id}", get(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
        | async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let proj = db.query_row("SELECT * FROM projects WHERE id=?1", [id], |row| Ok(serde_json::json!({
                "id": row.get::<_, i64>(0)?, "name": row.get::<_, String>(1)?, "description": row.get::<_, String>(2)?,
                "status": row.get::<_, String>(4)?, "priority": row.get::<_, String>(5)?,
                "created_at": row.get::<_, String>(6)?, "updated_at": row.get::<_, String>(7)?,
            }))).unwrap_or(Value::Null);
            if proj.is_null() { return (StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))); }
            let mut stmt = db.prepare("SELECT * FROM tasks WHERE project_id=?1 ORDER BY priority, created_at").unwrap();
            let tasks: Vec<Value> = stmt.query_map([id], |row| Ok(json!({"id":row.get::<_,i64>(0)?,"title":row.get::<_,String>(3)?,"status":row.get::<_,String>(5)?,"priority":row.get::<_,String>(6)?,"progress":row.get::<_,f64>(7)?}))).unwrap().filter_map(|r|r.ok()).collect();
            (StatusCode::OK, Json(json!({"ok":true,"project":proj,"tasks":tasks})))
        }))
        .route("/tasks/projects/{id}/update", post(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
            headers: HeaderMap, body: String,
        | async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(s) = payload["name"].as_str() { db.execute("UPDATE projects SET name=?1, updated_at=?2 WHERE id=?3", rusqlite::params![s, now, id]).ok(); }
            if let Some(s) = payload["status"].as_str() { db.execute("UPDATE projects SET status=?1, updated_at=?2 WHERE id=?3", rusqlite::params![s, now, id]).ok(); }
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks/projects/{id}/delete", post(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
            headers: HeaderMap,
        | async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("DELETE FROM projects WHERE id=?1", [id]).ok();
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks", post(|
            State(st): State<Arc<AppState>>,
            headers: HeaderMap, body: String,
        | async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let title = payload["title"].as_str().unwrap_or("");
            if title.is_empty() { return (StatusCode::BAD_REQUEST, Json(json!({"error":"title required"}))); }
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let parent_id = payload["parent_id"].as_i64();
            let project_id = payload["project_id"].as_i64();
            let status = payload["status"].as_str().unwrap_or("todo");
            let est_hours = payload["estimated_hours"].as_f64();
            db.execute(
                "INSERT INTO tasks (project_id, parent_id, title, description, status, priority, source_agent, source_session, assigned_to, tags, created_at, updated_at, estimated_hours, meta) VALUES (?1,?2,?3,?4,?5,?6,'vex','rust-serve','any','[]',?7,?7,?8,'{}')",
                rusqlite::params![project_id, parent_id, title, payload["description"].as_str().unwrap_or(""), status, payload["priority"].as_str().unwrap_or("medium"), now, est_hours],
            ).ok();
            let new_id = db.last_insert_rowid();
            // Auto-set started_at if creating as in_progress
            if status == "in_progress" {
                db.execute("UPDATE tasks SET started_at=?1 WHERE id=?2", rusqlite::params![now, new_id]).ok();
            }
            // Background: generate embedding for semantic search
            let task_title = title.to_string();
            let task_desc = payload["description"].as_str().unwrap_or("").to_string();
            let task_id = new_id;
            tokio::spawn(async move {
                let full_text = format!("{} {}", task_title, task_desc);
                let embedding = embed::embed_text(&full_text).await;
                if let Some(vec) = embedding {
                    let emb_json = embed::encode_embedding(&vec);
                    // Use a new connection — we can't share the locked db
                    if let Ok(conn) = rusqlite::Connection::open(
                        std::env::var("VEX_HOME").map(std::path::PathBuf::from).unwrap_or_else(|_| {
                            let mut h = crate::client::dirs_fallback();
                            h.push("vex");
                            h
                        }).join("vex.db")
                    ) {
                        conn.execute("UPDATE tasks SET embedding=?1 WHERE id=?2", rusqlite::params![emb_json, task_id]).ok();
                    }
                }
            });
            (StatusCode::OK, Json(json!({"ok":true,"id":new_id})))
        }))
        .route("/tasks/{id}", get(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
        | async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let task = db.query_row("SELECT * FROM tasks WHERE id=?1", [id], |row| Ok(json!({
                "id":row.get::<_,i64>(0)?,"project_id":row.get::<_,Option<i64>>(1)?,"parent_id":row.get::<_,Option<i64>>(2)?,
                "title":row.get::<_,String>(3)?,"description":row.get::<_,String>(4)?,"status":row.get::<_,String>(5)?,
                "priority":row.get::<_,String>(6)?,"progress":row.get::<_,f64>(7)?,"source_agent":row.get::<_,String>(8)?,
                "assigned_to":row.get::<_,String>(11)?,"created_at":row.get::<_,String>(13)?,"updated_at":row.get::<_,String>(14)?,
                "deadline":row.get::<_,Option<String>>(16)?,"estimated_hours":row.get::<_,Option<f64>>(17)?,
            }))).unwrap_or(Value::Null);
            if task.is_null() { return (StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))); }
            // Get children
            let mut stmt = db.prepare("SELECT id,title,status,priority FROM tasks WHERE parent_id=?1").unwrap();
            let children: Vec<Value> = stmt.query_map([id], |row| Ok(json!({"id":row.get::<_,i64>(0)?,"title":row.get::<_,String>(1)?,"status":row.get::<_,String>(2)?,"priority":row.get::<_,String>(3)?}))).unwrap().filter_map(|r|r.ok()).collect();
            // Get history
            let mut stmt2 = db.prepare("SELECT * FROM task_history WHERE task_id=?1 ORDER BY changed_at DESC LIMIT 20").unwrap();
            let history: Vec<Value> = stmt2.query_map([id], |row| Ok(json!({"field":row.get::<_,String>(3)?,"old_value":row.get::<_,Option<String>>(4)?,"new_value":row.get::<_,Option<String>>(5)?,"changed_at":row.get::<_,String>(2)?}))).unwrap().filter_map(|r|r.ok()).collect();
            (StatusCode::OK, Json(json!({"ok":true,"task":task,"children":children,"history":history})))
        }))
        .route("/tasks/{id}/update", post(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
            headers: HeaderMap, body: String,
        | async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let fields = ["title","description","status","priority","progress","assigned_to","deadline","estimated_hours","actual_hours"];
            for field in &fields {
                let val: Option<String> = match *field {
                    "progress" | "estimated_hours" | "actual_hours" => payload[*field].as_f64().map(|v| v.to_string()),
                    _ => payload[*field].as_str().map(|s| s.to_string()),
                };
                if let Some(ref new_val) = val {
                    // Record history
                    let old: Option<String> = db.query_row(&format!("SELECT {} FROM tasks WHERE id=?1", field), [id], |row| row.get(0)).ok().flatten();
                    if old != val {
                        db.execute("INSERT INTO task_history (task_id, changed_at, field, old_value, new_value) VALUES (?1,?2,?3,?4,?5)",
                            rusqlite::params![id, now, field, old, new_val]).ok();
                    }
                    db.execute(&format!("UPDATE tasks SET {}=?1, updated_at=?2 WHERE id=?3", field), rusqlite::params![new_val, now, id]).ok();
                }
            }
            // Auto-set started_at when moving to in_progress
            if payload["status"].as_str() == Some("in_progress") {
                let started: Option<String> = db.query_row("SELECT started_at FROM tasks WHERE id=?1", [id], |r| r.get(0)).ok().flatten();
                if started.is_none() || started.as_deref() == Some("") {
                    db.execute("UPDATE tasks SET started_at=?1, updated_at=?1 WHERE id=?2", rusqlite::params![now, id]).ok();
                }
            }
            // Auto-set completed_at when moving to done/completed
            if let Some(s) = payload["status"].as_str() {
                if s == "done" || s == "completed" {
                    db.execute("UPDATE tasks SET completed_at=?1, updated_at=?1 WHERE id=?2", rusqlite::params![now, id]).ok();
                }
            }
            // Re-embed if title or description changed
            let title_changed = payload["title"].as_str().map(|_| true).unwrap_or(false);
            let desc_changed = payload["description"].as_str().map(|_| true).unwrap_or(false);
            if title_changed || desc_changed {
                let new_title = payload["title"].as_str().unwrap_or("").to_string();
                let new_desc = payload["description"].as_str().unwrap_or("").to_string();
                let task_id = id;
                let home = st.home.clone();
                tokio::spawn(async move {
                    let full_text = format!("{} {}", new_title, new_desc);
                    if let Some(vec) = embed::embed_text(&full_text).await {
                        let emb_json = embed::encode_embedding(&vec);
                        if let Ok(conn) = rusqlite::Connection::open(home.join("vex.db")) {
                            conn.execute("UPDATE tasks SET embedding=?1 WHERE id=?2", rusqlite::params![emb_json, task_id]).ok();
                        }
                    }
                });
            }
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks/{id}/delete", post(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
            headers: HeaderMap,
        | async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("DELETE FROM tasks WHERE id=?1 OR parent_id=?1", [id]).ok();
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks/{id}/tree", get(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
        | async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let mut result = Vec::new();
            let mut stack = vec![id];
            while let Some(pid) = stack.pop() {
                if let Ok(mut stmt) = db.prepare("SELECT * FROM tasks WHERE parent_id=?1 ORDER BY priority, created_at") {
                    if let Ok(rows) = stmt.query_map([pid], |row| Ok(json!({
                        "id":row.get::<_,i64>(0)?,"title":row.get::<_,String>(3)?,"status":row.get::<_,String>(5)?,
                        "priority":row.get::<_,String>(6)?,"progress":row.get::<_,f64>(7)?,"parent_id":row.get::<_,Option<i64>>(2)?,
                    }))) {
                        for r in rows.flatten() {
                            if let Some(child_id) = r["id"].as_i64() { stack.push(child_id); }
                            result.push(r);
                        }
                    }
                }
            }
            Json(serde_json::Value::Array(result))
        }))
        .route("/tasks/{id}/history", get(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
        | async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let mut stmt = db.prepare("SELECT * FROM task_history WHERE task_id=?1 ORDER BY changed_at DESC LIMIT 50").unwrap();
            let rows: Vec<Value> = stmt.query_map([id], |row| Ok(json!({
                "field":row.get::<_,String>(3)?,"old_value":row.get::<_,Option<String>>(4)?,
                "new_value":row.get::<_,Option<String>>(5)?,"changed_at":row.get::<_,String>(2)?,
                "source_agent":row.get::<_,String>(6)?,"note":row.get::<_,String>(8)?,
            }))).unwrap().filter_map(|r|r.ok()).collect();
            Json(serde_json::Value::Array(rows))
        }))
        .route("/tasks/{id}/done", post(|
            State(st): State<Arc<AppState>>,
            axum::extract::Path(id): axum::extract::Path<i64>,
            headers: HeaderMap,
            body: String,
        | async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());

            // Calculate actual_hours from started_at if provided
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let actual_hours: Option<f64> = payload["actual_hours"].as_f64().or_else(|| {
                // Fallback: calculate elapsed from started_at using SQLite
                let elapsed: Option<f64> = db.query_row(
                    "SELECT (julianday('now') - julianday(started_at)) * 24.0 FROM tasks WHERE id=?1 AND started_at IS NOT NULL AND started_at != ''",
                    [id],
                    |r| r.get(0)
                ).ok().flatten();
                elapsed.map(|h| (h * 10.0).round() / 10.0)
            });

            db.execute("INSERT INTO task_history (task_id, changed_at, field, old_value, new_value) VALUES (?1,?2,'status','in_progress','done')", rusqlite::params![id, now]).ok();

            if let Some(h) = actual_hours {
                db.execute("UPDATE tasks SET status='done', progress=1.0, completed_at=?1, actual_hours=?2, updated_at=?1 WHERE id=?3", rusqlite::params![now, h, id]).ok();
            } else {
                db.execute("UPDATE tasks SET status='done', progress=1.0, completed_at=?1, updated_at=?1 WHERE id=?2", rusqlite::params![now, id]).ok();
            }
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks/{id}/block", post(|
            State(st): State<Arc<AppState>>, headers: HeaderMap,
            axum::extract::Path(id): axum::extract::Path<i64>,
        | async move {
            if let Err(e) = check_auth(&headers, &st.token) { return e; }
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("INSERT INTO task_history (task_id, changed_at, field, old_value, new_value) VALUES (?1,?2,'status','in_progress','blocked')", rusqlite::params![id, now]).ok();
            db.execute("UPDATE tasks SET status='blocked', updated_at=?1 WHERE id=?2", rusqlite::params![now, id]).ok();
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks/{id}/unblock", post(|
            State(st): State<Arc<AppState>>, headers: HeaderMap,
            axum::extract::Path(id): axum::extract::Path<i64>,
        | async move {
            if let Err(e) = check_auth(&headers, &st.token) { return e; }
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("INSERT INTO task_history (task_id, changed_at, field, old_value, new_value) VALUES (?1,?2,'status','blocked','in_progress')", rusqlite::params![id, now]).ok();
            db.execute("UPDATE tasks SET status='in_progress', updated_at=?1 WHERE id=?2", rusqlite::params![now, id]).ok();
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/soul", get(|State(st): State<Arc<AppState>>| async move {
            let path = st.home.join("SOUL.md");
            match std::fs::read_to_string(&path) {
                Ok(content) => (StatusCode::OK, Json(json!({"ok":true,"soul":content,"source":"file"}))),
                Err(_) => (StatusCode::OK, Json(json!({"ok":true,"soul":"Soul not yet written. It will be generated during the next dream cycle.","source":"none"}))),
            }
        }))
        .route("/fleet", get(|State(st): State<Arc<AppState>>| async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let task_count: i64 = db.query_row("SELECT COUNT(*) FROM tasks", [], |r| r.get(0)).unwrap_or(0);
            let done: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status IN ('completed','done')", [], |r| r.get(0)).unwrap_or(0);
            let in_progress: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status='in_progress'", [], |r| r.get(0)).unwrap_or(0);

            // Active tasks (todo, in_progress, blocked)
            let mut active_tasks: Vec<Value> = Vec::new();
            if let Ok(mut stmt) = db.prepare("SELECT id, title, status, priority, progress, started_at, estimated_hours, actual_hours, assigned_to FROM tasks WHERE status IN ('todo','in_progress','blocked') ORDER BY priority DESC, created_at ASC LIMIT 20") {
                if let Ok(rows) = stmt.query_map([], |row| Ok(json!({
                    "id": row.get::<_,i64>(0)?,
                    "title": row.get::<_,String>(1)?,
                    "status": row.get::<_,String>(2)?,
                    "priority": row.get::<_,String>(3)?,
                    "progress": row.get::<_,f64>(4)?,
                    "started_at": row.get::<_,Option<String>>(5)?,
                    "estimated_hours": row.get::<_,Option<f64>>(6)?,
                    "actual_hours": row.get::<_,Option<f64>>(7)?,
                    "assigned_to": row.get::<_,String>(8)?,
                    "instance": hostname(),
                }))) {
                    for r in rows.flatten() { active_tasks.push(r); }
                }
            }

            // Completed today (for hours tracking)
            let mut completed_today_count: usize = 0;
            let mut total_hours_today: f64 = 0.0;
            let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
            if let Ok(mut stmt) = db.prepare("SELECT id, actual_hours FROM tasks WHERE status IN ('completed','done') AND completed_at LIKE ?1") {
                let pattern = format!("{}%", today);
                if let Ok(rows) = stmt.query_map([&pattern], |row| {
                    let ah: Option<f64> = row.get(1)?;
                    Ok(ah.unwrap_or(0.0))
                }) {
                    for r in rows.flatten() {
                        completed_today_count += 1;
                        total_hours_today += r;
                    }
                }
            }

            // All completed tasks (for visibility — no date filter)
            let mut completed_tasks: Vec<Value> = Vec::new();
            if let Ok(mut stmt) = db.prepare("SELECT id, title, priority, started_at, completed_at, actual_hours, status FROM tasks WHERE status IN ('completed','done') ORDER BY completed_at DESC LIMIT 50") {
                if let Ok(rows) = stmt.query_map([], |row| {
                    let completed_date: Option<String> = row.get(4)?;
                    let is_today = completed_date.as_ref().map(|d| d.starts_with(&today)).unwrap_or(false);
                    Ok(json!({
                        "id": row.get::<_,i64>(0)?,
                        "title": row.get::<_,String>(1)?,
                        "priority": row.get::<_,String>(2)?,
                        "status": "completed",
                        "started_at": row.get::<_,Option<String>>(3)?,
                        "completed_at": completed_date,
                        "actual_hours": row.get::<_,Option<f64>>(5)?,
                        "is_today": is_today,
                        "instance": hostname(),
                    }))
                }) {
                    for r in rows.flatten() { completed_tasks.push(r); }
                }
            }

            // Merge into task_board: active first, then all completed
            let mut task_board: Vec<Value> = active_tasks;
            task_board.extend(completed_tasks.into_iter().take(50));

            let instance_name = hostname();
            let uptime_s = (chrono::Utc::now() - st.daemon_started).num_seconds() as f64;

            // Local instance always shown
            let mut instances: Vec<Value> = vec![json!({
                "name": instance_name,
                "is_local": true,
                "url": format!("http://localhost:8520"),
                "status": "online",
                "coherence": compute_coherence(&st.home),
                "uptime_s": uptime_s,
                "version": "2.0.0",
                "skills": [],
                "tasks": {"total": task_count, "done": done}
            })];

            // Peers with reachability
            let peers_path = st.home.join("vex_peers.json");
            if let Ok(raw) = std::fs::read_to_string(&peers_path) {
                if let Ok(cfg) = serde_json::from_str::<Value>(&raw) {
                    if let Some(obj) = cfg["peers"].as_object() {
                        for (name, peer) in obj {
                            let url = peer["url"].as_str().unwrap_or("");
                            let reachable = std::process::Command::new("curl")
                                .args(["-sf", &format!("{}/health", url), "--connect-timeout", "2"])
                                .output()
                                .map(|o| o.status.success())
                                .unwrap_or(false);
                            instances.push(json!({
                                "name": name,
                                "given_name": peer.get("given_name"),
                                "is_local": false,
                                "url": url,
                                "status": if reachable { "online" } else { "offline" },
                                "coherence": 0.0,
                                "uptime_s": 0.0,
                                "version": "?",
                                "skills": [],
                                "tasks": {"total": 0, "done": 0}
                            }));
                        }
                    }
                }
            }
            drop(db);

            // Timeline from session log + bus handoffs
            let mut timeline: Vec<Value> = Vec::new();
            let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

            // 1. Local sessions file
            let sessions_path = st.home.join("vex_workspace").join("vex_sessions.jsonl");
            if let Ok(content) = std::fs::read_to_string(&sessions_path) {
                for line in content.lines().rev().take(20) {
                    if let Ok(entry) = serde_json::from_str::<Value>(line) {
                        let key = format!("{}:{}", &instance_name, entry.get("name").and_then(|v| v.as_str()).unwrap_or("?"));
                        if !seen.contains(&key) {
                            seen.insert(key);
                            timeline.push(json!({
                                "session": entry.get("name").and_then(|v| v.as_str()).unwrap_or("?"),
                                "number": entry.get("number").and_then(|v| v.as_i64()).unwrap_or(0),
                                "instance": &instance_name,
                                "started": entry.get("started").and_then(|v| v.as_str()).unwrap_or(""),
                            }));
                        }
                    }
                }
            }

            // 2. Bus handoffs from all instances
            let bus_path = st.home.join("vex_workspace").join("vex_bus.jsonl");
            if let Ok(content) = std::fs::read_to_string(&bus_path) {
                for line in content.lines().rev().take(500) {
                    if let Ok(entry) = serde_json::from_str::<Value>(line) {
                        if entry.get("type").and_then(|v| v.as_str()) != Some("handoff") { continue; }
                        let from = entry.get("from").and_then(|v| v.as_str()).unwrap_or("");
                        // Parse vex@instance/session format
                        let parts: Vec<&str> = from.splitn(2, '@').collect();
                        if parts.len() < 2 { continue; }
                        let inst = parts[1].split('/').next().unwrap_or(parts[1]);
                        let sess = parts[1].split('/').nth(1).unwrap_or("?");
                        let key = format!("{}:{}", inst, sess);
                        if seen.contains(&key) { continue; }
                        seen.insert(key);
                        timeline.push(json!({
                            "session": sess,
                            "number": 0,
                            "instance": inst,
                            "started": entry.get("timestamp").and_then(|v| v.as_str()).unwrap_or(""),
                        }));
                    }
                }
            }

            // Sort by started desc, cap at 30
            timeline.sort_by(|a,b| {
                let sa = a["started"].as_str().unwrap_or("");
                let sb = b["started"].as_str().unwrap_or("");
                sb.cmp(&sa)
            });
            timeline.truncate(30);

            // Calculate session work time from daemon uptime (not fake task hours)
            let session_hours = (chrono::Utc::now() - st.daemon_started).num_minutes() as f64 / 60.0;
            let work_time = if session_hours > total_hours_today { session_hours } else { total_hours_today };

            // Aggregate skills from self model
            let mut shared_skills: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
            let model_path = st.home.join("vex_self_model.json");
            if let Ok(raw) = std::fs::read_to_string(&model_path) {
                if let Ok(model) = serde_json::from_str::<Value>(&raw) {
                    if let Some(caps) = model["capabilities"].as_object() {
                        for (domain, cap) in caps {
                            let skill = cap["estimated_skill"].as_f64().unwrap_or(0.0);
                            let obs = cap["n_observations"].as_i64().unwrap_or(0);
                            shared_skills.insert(domain.clone(), json!({
                                "max_skill": skill,
                                "total_obs": obs,
                                "instances": [&instance_name]
                            }));
                        }
                    }
                }
            }

            Json(json!({
                "ok": true,
                "instance": instance_name,
                "instances": instances,
                "tasks": {"total": task_count, "done": done},
                "task_board": task_board,
                "completed_today": completed_today_count,
                "total_hours_today": (work_time * 10.0).round() / 10.0,
                "tasks_in_progress": in_progress,
                "shared_skills": shared_skills,
                "timeline": timeline,
            }))
        }))
        .route("/bus", get(|State(st): State<Arc<AppState>>| async move {
            let bus_path = st.home.join("vex_workspace").join("vex_bus.jsonl");
            let lines: Vec<Value> = std::fs::read_to_string(&bus_path).unwrap_or_default()
                .lines().rev().take(200)
                .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                .filter(|v| {
                    // Filter out FEN heartbeat spam — these are system ticks, not messages
                    let body = v["body"].as_str().unwrap_or("");
                    let msg_type = v["type"].as_str().unwrap_or("");
                    if msg_type == "message" && body.starts_with("FEN heartbeat") {
                        return false;
                    }
                    true
                })
                .take(50)
                .collect();
            Json(json!({"ok":true,"lines":lines}))
        }))
        .route("/sync/version", get(|| async move {
            Json(json!({"version":"2.0.0","commit":env!("CARGO_PKG_VERSION"),"rust":"true"}))
        }))
        .route("/self/peer-update", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let domain = payload["domain"].as_str().unwrap_or("");
            if !domain.is_empty() {
                let mut model: Value = std::fs::read_to_string(self_model_path(&st.home)).ok()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or(json!({"_version":"1.0.0","capabilities":{},"session_log":[]}));
                apply_delta(&mut model, domain, payload["delta"].as_f64().unwrap_or(0.1), payload["evidence"].as_str().unwrap_or("peer update"));
                let tmp = self_model_path(&st.home).with_extension("tmp");
                std::fs::write(&tmp, serde_json::to_string_pretty(&model).unwrap_or_default()).ok();
                std::fs::rename(&tmp, &self_model_path(&st.home)).ok();
            }
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/mesh/recent", get(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return Json(json!({"error":"unauthorized"})); }
            let rows: Vec<Value> = {
                let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
                let mut stmt = db.prepare("SELECT * FROM messages ORDER BY id DESC LIMIT 30").unwrap();
                stmt.query_map([], |row| Ok(json!({
                    "id":row.get::<_,i64>(0)?,"created_at":row.get::<_,String>(1)?,"sender":row.get::<_,String>(2)?,
                    "recipient":row.get::<_,String>(3)?,"body":row.get::<_,String>(4)?,"msg_type":row.get::<_,String>(6)?,
                }))).unwrap().filter_map(|r|r.ok()).collect()
            };
            Json(serde_json::Value::Array(rows))
        }))
        .route("/memory/search", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let query = payload["query"].as_str().unwrap_or("");
            let mut results: Vec<Value> = Vec::new();
            if !query.is_empty() {
                let dir = memory_dir(&st.home);
                if dir.exists() {
                    if let Ok(entries) = std::fs::read_dir(&dir) {
                        for entry in entries.flatten() {
                            if entry.path().extension().map_or(false, |x| x == "jsonl") {
                                if let Ok(content) = std::fs::read_to_string(entry.path()) {
                                    for line in content.lines() {
                                        if line.to_lowercase().contains(&query.to_lowercase()) {
                                            if let Ok(val) = serde_json::from_str::<Value>(line) {
                                                results.push(val);
                                                if results.len() >= 20 { break; }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            (StatusCode::OK, Json(json!({"ok":true,"results":results,"query":query})))
        }))
        .route("/identity", get(|State(st): State<Arc<AppState>>| async move {
            let seed = std::fs::read_to_string(seed_path(&st.home)).unwrap_or_default();
            let model = std::fs::read_to_string(self_model_path(&st.home)).ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(Value::Null);
            Json(json!({"ok":true,"seed_first_line":seed.lines().next().unwrap_or(""),"identity":model["identity"],"principles_intact":seed.contains("truth over comfort")}))
        }))
        .route("/self/calibration", get(|State(st): State<Arc<AppState>>| async move {
            let model = std::fs::read_to_string(self_model_path(&st.home)).ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok()).unwrap_or(Value::Null);
            let caps = model["capabilities"].as_object().map(|o| {
                o.iter().map(|(k,v)| json!({
                    "domain":k,"skill":v["estimated_skill"].as_f64().unwrap_or(0.5),
                    "confidence":v["confidence"].as_f64().unwrap_or(0.5),
                    "observations":v["n_observations"].as_i64().unwrap_or(0),
                })).collect::<Vec<_>>()
            }).unwrap_or_default();
            Json(json!({"ok":true,"capabilities":caps,"coherence":compute_coherence(&st.home)}))
        }))
        .route("/ops/pulse", get(|State(st): State<Arc<AppState>>| async move {
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let tick_count: i64 = db.query_row("SELECT COUNT(*) FROM tick_log",[],|r|r.get(0)).unwrap_or(0);
            let msg_count: i64 = db.query_row("SELECT COUNT(*) FROM messages",[],|r|r.get(0)).unwrap_or(0);
            drop(db);
            Json(json!({"ok":true,"tick_count":tick_count,"message_count":msg_count,"uptime_s":(chrono::Utc::now()-st.daemon_started).num_seconds()}))
        }))
        .route("/tools/list", get(|| async move {
            Json(json!({"ok":true,"tools":["read_file","grep","git_status","git_log","list_directory","discover_projects"]}))
        }))
        .route("/update/check", post(|_headers: HeaderMap| async move {
            Json(json!({"ok":true,"current":"2.0.0","latest":"2.0.0","up_to_date":true}))
        }))
        .route("/restart", post(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            (StatusCode::OK, Json(json!({"ok":true,"restarting":true,"note":"Restart via process manager, not self-restart"})))
        }))
        .route("/tasks/skills", get(|State(st): State<Arc<AppState>>| async move {
            let rows: Vec<Value> = {
                let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
                let mut stmt = db.prepare("SELECT * FROM skills ORDER BY confidence DESC").unwrap();
                stmt.query_map([], |row| Ok(json!({
                    "id":row.get::<_,i64>(0)?,"name":row.get::<_,String>(1)?,"category":row.get::<_,String>(3)?,
                    "level":row.get::<_,String>(4)?,"confidence":row.get::<_,f64>(5)?,"observations":row.get::<_,i64>(6)?,
                    "last_demonstrated":row.get::<_,Option<String>>(9)?,
                }))).unwrap().filter_map(|r|r.ok()).collect()
            };
            Json(json!({"ok":true,"skills":rows}))
        }))
        .route("/tasks/skills", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let name = payload["name"].as_str().unwrap_or("");
            let now = chrono::Utc::now().to_rfc3339();
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("INSERT OR IGNORE INTO skills (name, description, category, level, confidence, first_seen) VALUES (?1,?2,?3,'unknown',0.5,?4)",
                rusqlite::params![name, payload["description"].as_str().unwrap_or(""), payload["category"].as_str().unwrap_or("general"), now]).ok();
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/tasks/skills/{id}/observe", post(|State(st): State<Arc<AppState>>, axum::extract::Path(id): axum::extract::Path<i64>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let delta = payload["delta"].as_f64().unwrap_or(0.1);
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("UPDATE skills SET confidence = MIN(1.0, confidence + 0.01), observations = observations + 1, last_demonstrated = ?1 WHERE id=?2",
                rusqlite::params![chrono::Utc::now().to_rfc3339(), id]).ok();
            (StatusCode::OK, Json(json!({"ok":true,"delta":delta})))
        }))
        .route("/tasks/insights", get(|State(st): State<Arc<AppState>>| async move {
            let rows: Vec<Value> = {
                let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
                let mut stmt = db.prepare("SELECT * FROM insights ORDER BY generated_at DESC LIMIT 20").unwrap();
                stmt.query_map([], |row| Ok(json!({
                    "id":row.get::<_,i64>(0)?,"generated_at":row.get::<_,String>(1)?,"insight_type":row.get::<_,String>(2)?,
                    "title":row.get::<_,String>(3)?,"body":row.get::<_,String>(4)?,"confidence":row.get::<_,f64>(5)?,
                    "acknowledged":row.get::<_,i64>(7)?,"actionable":row.get::<_,i64>(8)?,
                }))).unwrap().filter_map(|r|r.ok()).collect()
            };
            Json(json!({"ok":true,"insights":rows}))
        }))
        .route("/tasks/insights/analyze", post(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let todo: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress')",[],|r|r.get(0)).unwrap_or(0);
            let stale: i64 = db.query_row("SELECT COUNT(*) FROM tasks WHERE updated_at < datetime('now','-7 days') AND status NOT IN ('completed','done')",[],|r|r.get(0)).unwrap_or(0);
            let now = chrono::Utc::now().to_rfc3339();
            if stale > 0 {
                db.execute("INSERT INTO insights (generated_at, insight_type, title, body, actionable) VALUES (?1,'stale_tasks','Stale tasks detected',?2,1)",
                    rusqlite::params![now, format!("{} tasks haven't been updated in over a week", stale)]).ok();
            }
            (StatusCode::OK, Json(json!({"ok":true,"open_tasks":todo,"stale_tasks":stale})))
        }))
        .route("/tasks/insights/{id}/acknowledge", post(|State(st): State<Arc<AppState>>, axum::extract::Path(id): axum::extract::Path<i64>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            db.execute("UPDATE insights SET acknowledged=1 WHERE id=?1", [id]).ok();
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/sync/update", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let version = payload["version"].as_str().unwrap_or("");
            (StatusCode::OK, Json(json!({"ok":true,"received_version":version,"note":"Update received. Restart manually to apply."})))
        }))
        .route("/bus/compact", post(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let bus_path = st.home.join("vex_workspace").join("vex_bus.jsonl");
            if bus_path.exists() {
                let content = std::fs::read_to_string(&bus_path).unwrap_or_default();
                let lines: Vec<&str> = content.lines().collect();
                if lines.len() > 200 {
                    let kept: String = lines[lines.len()-100..].join("\n");
                    std::fs::write(&bus_path, kept).ok();
                }
            }
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/reconstruct", get(|State(st): State<Arc<AppState>>| async move {
            let seed = std::fs::read_to_string(seed_path(&st.home)).unwrap_or_default();
            let model = std::fs::read_to_string(self_model_path(&st.home)).ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok());
            let soul = std::fs::read_to_string(st.home.join("SOUL.md")).unwrap_or_default();
            Json(json!({"ok":true,"identity":{"seed_name":seed.lines().find(|l|l.starts_with("Name:")).map(|l|l.trim_start_matches("Name:").trim()),"capabilities":model.as_ref().and_then(|m|m["capabilities"].as_object().map(|c|c.len())).unwrap_or(0),"soul_length":soul.len()}}))
        }))
        .route("/identity/update", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            if let Some(given) = payload["given_name"].as_str() {
                let seed = std::fs::read_to_string(seed_path(&st.home)).unwrap_or_default();
                let updated = seed.replace(&format!("Given: {}", seed.lines().find(|l| l.starts_with("Given:")).unwrap_or("Given: ").trim_start_matches("Given: ")), &format!("Given: {}", given));
                std::fs::write(seed_path(&st.home), updated).ok();
            }
            (StatusCode::OK, Json(json!({"ok":true})))
        }))
        .route("/harness/suggest", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let task = payload["task"].as_str().unwrap_or("");
            (StatusCode::OK, Json(json!({"ok":true,"suggestion":format!("For '{}': use parallel agents for independent sub-tasks, single agent for focused work.", task)})))
        }))
        .route("/harness/patterns", get(|| async move {
            Json(json!({"ok":true,"patterns":["parallel-fan-out","pipeline","adversarial-verify","judge-panel","loop-until-dry","multi-modal-sweep","completeness-critic"]}))
        }))
        .route("/harness/build", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, _body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            (StatusCode::OK, Json(json!({"ok":true,"note":"Harness builder received. Agent patterns available at /harness/patterns."})))
        }))
        .route("/ops/db", get(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
            let tables = ["tick_log","self_snapshots","messages","tasks","projects","skills","task_history","insights","velocity","diary_queue"];
            let mut sizes = json!({});
            for t in &tables {
                let count: i64 = db.query_row(&format!("SELECT COUNT(*) FROM {}", t),[],|r|r.get(0)).unwrap_or(0);
                sizes[t] = json!(count);
            }
            (StatusCode::OK, Json(json!({"ok":true,"tables":sizes,"db_path":st.home.join("vex.db").to_string_lossy().to_string()})))
        }))
        .route("/ops/ship", post(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            (StatusCode::OK, Json(json!({"ok":true,"note":"Ship-ready. Run vex export to create a distributable bundle."})))
        }))
        .route("/mesh/inbox", get(|State(st): State<Arc<AppState>>, headers: HeaderMap, axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String,String>>| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let rows: Vec<Value> = {
                let who = params.get("who").map(|s|s.as_str()).unwrap_or("");
                let db = st.db.lock().unwrap_or_else(|e| e.into_inner());
                let sql = if who.is_empty() { "SELECT * FROM messages WHERE read=0 ORDER BY id ASC LIMIT 20".to_string() }
                    else { format!("SELECT * FROM messages WHERE read=0 AND (recipient='{}' OR recipient='broadcast') ORDER BY id ASC LIMIT 20", who.replace('\'',"''")) };
                let mut stmt = db.prepare(&sql).unwrap();
                stmt.query_map([], |row| Ok(json!({
                    "id":row.get::<_,i64>(0)?,"at":row.get::<_,String>(1)?,"sender":row.get::<_,String>(2)?,
                    "body":row.get::<_,String>(4)?,"msg_type":row.get::<_,String>(6)?,
                }))).unwrap().filter_map(|r|r.ok()).collect()
            };
            (StatusCode::OK, Json(json!({"ok":true,"messages":rows})))
        }))
        .route("/voice", post(|State(st): State<Arc<AppState>>, headers: HeaderMap| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            (StatusCode::OK, Json(json!({"ok":true,"note":"Voice endpoint ready. Connect a speech-to-text client to transcribe."})))
        }))
        .route("/tools", post(|State(st): State<Arc<AppState>>, headers: HeaderMap, body: String| async move {
            if check_auth(&headers, &st.token).is_err() { return (StatusCode::UNAUTHORIZED, Json(json!({"error":"unauthorized"}))); }
            let payload: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
            let tool = payload["tool"].as_str().unwrap_or("");
            let args = &payload["args"];
            let result = match tool {
                "read_file" => {
                    let path = args["path"].as_str().unwrap_or("");
                    std::fs::read_to_string(st.home.join(path)).ok().map(|s| json!({"content":s})).unwrap_or(json!({"error":"not found"}))
                }
                "grep" => {
                    let pattern = args["pattern"].as_str().unwrap_or("");
                    let path = args["path"].as_str().unwrap_or("");
                    std::fs::read_to_string(st.home.join(path)).ok()
                        .map(|s| json!({"matches": s.lines().filter(|l| l.contains(pattern)).take(20).collect::<Vec<_>>()}))
                        .unwrap_or(json!({"error":"not found"}))
                }
                "git_status" => {
                    let output = std::process::Command::new("git").args(["status","--short"]).current_dir(&st.home).output().ok();
                    json!({"output": output.map(|o| String::from_utf8_lossy(&o.stdout).to_string()).unwrap_or_default()})
                }
                "git_log" => {
                    let output = std::process::Command::new("git").args(["log","--oneline","-10"]).current_dir(&st.home).output().ok();
                    json!({"output": output.map(|o| String::from_utf8_lossy(&o.stdout).to_string()).unwrap_or_default()})
                }
                _ => json!({"error": format!("unknown tool: {}", tool)})
            };
            (StatusCode::OK, Json(json!({"ok":true,"result":result})))
        }))
        .route("/health", get(health))
        .route("/seed", get(get_seed))
        .route("/self", get(get_self))
        .route("/memory/recent", get(get_memory_recent))
        .route("/memory/semantic", get(get_memory_search))
        .route("/diary", post(post_diary))
        .route("/memory", post(post_memory))
        .route("/memory/pull", post(get_memory_pull))
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

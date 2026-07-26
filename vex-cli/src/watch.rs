use rusqlite::Connection;
use std::path::PathBuf;
use std::time::Duration;

/// Poll file modification times every `interval_secs` and snapshot
/// vex_self_model.json to SQLite when it changes. Also watches
/// vex_seed.txt for identity drift.
pub async fn run(
    home: PathBuf,
    interval_secs: u64,
) -> Result<(), Box<dyn std::error::Error>> {
    let self_path = home.join("vex_self_model.json");
    let seed_path = home.join("vex_seed.txt");
    let db_path = home.join("vex.db");
    let interval = Duration::from_secs(interval_secs);

    let mut last_self_mtime = mtime(&self_path);
    let mut last_seed_mtime = mtime(&seed_path);

    eprintln!(
        "[watch] armed — watching {} every {}s",
        home.display(),
        interval_secs
    );

    loop {
        tokio::time::sleep(interval).await;

        let self_mtime = mtime(&self_path);
        let seed_mtime = mtime(&seed_path);

        if self_mtime != last_self_mtime || seed_mtime != last_seed_mtime {
            let what = if self_mtime != last_self_mtime && seed_mtime != last_seed_mtime {
                "self-model + seed"
            } else if self_mtime != last_self_mtime {
                "self-model"
            } else {
                "seed"
            };

            eprintln!("[watch] {} changed — snapshotting", what);

            if let Ok(conn) = Connection::open(&db_path) {
                // Snapshot self-model
                if let Ok(content) = std::fs::read_to_string(&self_path) {
                    if serde_json::from_str::<serde_json::Value>(&content).is_ok() {
                        let now = chrono::Utc::now().to_rfc3339();
                        conn.execute(
                            "INSERT INTO self_snapshots (created_at, json_blob, reason) VALUES (?1, ?2, 'watch')",
                            rusqlite::params![now, content],
                        ).ok();
                    }
                }

                // Write tick
                let coherence = compute_coherence_from_file(&self_path);
                let now = chrono::Utc::now().to_rfc3339();
                conn.execute(
                    "INSERT INTO tick_log (tick_at, mps_coherence, mps_drift, session_active, note) VALUES (?1, ?2, 0.0, 0, 'watch')",
                    rusqlite::params![now, coherence],
                ).ok();

                eprintln!("[watch] snapshot taken — coherence {:.4}", coherence);
            }

            last_self_mtime = self_mtime;
            last_seed_mtime = seed_mtime;
        }
    }
}

fn mtime(path: &PathBuf) -> Option<std::time::SystemTime> {
    std::fs::metadata(path)
        .ok()
        .and_then(|m| m.modified().ok())
}

fn compute_coherence_from_file(path: &PathBuf) -> f64 {
    match std::fs::read_to_string(path) {
        Ok(content) => match serde_json::from_str::<serde_json::Value>(&content) {
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

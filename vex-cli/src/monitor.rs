use crate::client::Client;
use std::time::Duration;

/// Poll the daemon inbox every `interval_secs` and print new messages.
/// Replaces the bash vex_monitor.sh polling loop.
/// Uses ID-based dedup to prevent double-fires from same-second timestamps.
pub async fn run(client: &Client, interval_secs: u64) -> Result<(), crate::client::Error> {
    let mut last_id: i64 = 0;
    let interval = Duration::from_secs(interval_secs);

    eprintln!(
        "[monitor] armed — watching {} every {}s",
        client.base_url(),
        interval_secs
    );

    loop {
        // Always fetch unread messages; the daemon marks them read on fetch
        // (we pass mark_read=false so they stay unread for other consumers).
        // Use since=timestamp from our last seen message for efficiency,
        // then client-side dedup by id to prevent double-fires.
        let since_param = if last_id > 0 {
            // Fetch a window around our last seen message to catch any stragglers
            format!("/message/inbox?mark_read=false")
        } else {
            "/message/inbox?mark_read=false".to_string()
        };

        match client.get::<serde_json::Value>(&since_param).await {
            Ok(val) => {
                if let Some(msgs) = val.as_array() {
                    for m in msgs {
                        let id = m["id"].as_i64().unwrap_or(0);
                        if id <= last_id {
                            continue; // already seen — id-based dedup
                        }
                        last_id = id;

                        let ts = m["created_at"]
                            .as_str()
                            .map(|s| &s[..s.len().min(19)])
                            .unwrap_or("?");
                        let sender = m["sender"].as_str().unwrap_or("?");
                        let body = m["body"].as_str().unwrap_or("");
                        let display = if body.len() > 150 {
                            format!("{}…", &body[..150])
                        } else {
                            body.to_string()
                        };
                        println!("[monitor] [{}] {}: {}", ts, sender, display);
                    }
                }
            }
            Err(crate::client::Error::Api { status, .. }) if status == 401 => {
                eprintln!("[monitor] auth failed — check ~/.vex_token");
            }
            Err(_) => {
                // Daemon unreachable — silently retry
            }
        }

        tokio::time::sleep(interval).await;
    }
}

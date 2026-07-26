use crate::client::Client;

pub async fn cmd_diary(client: &Client, entry: &str) -> Result<(), crate::client::Error> {
    let body = serde_json::json!({"entry": entry});
    let resp: serde_json::Value = client.post("/diary", &body).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        println!("Written.");
    } else {
        eprintln!(
            "Error: {}",
            resp.get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    Ok(())
}

pub async fn cmd_dream(client: &Client) -> Result<(), crate::client::Error> {
    let resp: serde_json::Value = client.post("/dream", &serde_json::json!({})).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        println!(
            "{}",
            resp.get("reflection")
                .and_then(|v| v.as_str())
                .unwrap_or("Dreamed.")
        );
    } else {
        eprintln!(
            "Error: {}",
            resp.get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    Ok(())
}

pub async fn cmd_introspect(client: &Client) -> Result<(), crate::client::Error> {
    let resp: serde_json::Value = client.post("/introspect", &serde_json::json!({})).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        println!(
            "{}",
            resp.get("insight")
                .and_then(|v| v.as_str())
                .unwrap_or("Introspected.")
        );
        if let Some(patterns) = resp.get("patterns").and_then(|v| v.as_array()) {
            if !patterns.is_empty() {
                println!("\nObserved patterns:");
                for p in patterns {
                    if let Some(s) = p.as_str() {
                        println!("  • {}", s);
                    }
                }
            }
        }
    } else {
        eprintln!(
            "Error: {}",
            resp.get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    Ok(())
}

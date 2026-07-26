use crate::client::Client;

pub async fn cmd_peers(client: &Client) -> Result<(), crate::client::Error> {
    let resp: serde_json::Value = client.get("/peers").await?;
    if !resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        eprintln!(
            "Error: {}",
            resp.get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    let peers = resp.get("peers").and_then(|v| v.as_array());
    if peers.map_or(true, |p| p.is_empty()) {
        println!("No peers configured.");
        println!("\nAdd one with: vex peer-add <name> <url> <token> [given_name]");
        return Ok(());
    }
    for p in peers.unwrap() {
        let reachable = p.get("reachable").and_then(|v| v.as_bool()).unwrap_or(false);
        let icon = if reachable { "✓" } else { "✗" };
        let name = p["name"].as_str().unwrap_or("?");
        let given = p.get("given_name").and_then(|v| v.as_str()).unwrap_or("");
        let display = if !given.is_empty() {
            format!("Vex {}", given)
        } else {
            format!("Vex ({})", name)
        };
        let url = p["url"].as_str().unwrap_or("?");
        let extra = if reachable {
            format!(
                " v{}  uptime {:.0}s",
                p.get("version").and_then(|v| v.as_str()).unwrap_or("?"),
                p.get("uptime_s").and_then(|v| v.as_f64()).unwrap_or(0.0)
            )
        } else {
            format!(
                " ({})",
                p.get("error")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown")
            )
        };
        println!("  {} {:28} {}{}", icon, display, url, extra);
    }
    Ok(())
}

pub async fn cmd_peer_add(
    client: &Client,
    name: &str,
    url: &str,
    token: &str,
    given_name: &str,
) -> Result<(), crate::client::Error> {
    let mut body = serde_json::json!({
        "name": name,
        "url": url,
        "token": token,
    });
    if !given_name.is_empty() {
        body["given_name"] = serde_json::Value::String(given_name.to_string());
    }
    let resp: serde_json::Value = client.post("/peers/add", &body).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        let peers: Vec<&str> = resp["peers"]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();
        let display = if !given_name.is_empty() {
            format!("Vex {}", given_name)
        } else {
            name.to_string()
        };
        println!("Peer '{}' added. Known peers: {}", display, peers.join(", "));
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

pub async fn cmd_peer_remove(client: &Client, name: &str) -> Result<(), crate::client::Error> {
    let body = serde_json::json!({"name": name});
    let resp: serde_json::Value = client.post("/peers/remove", &body).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        let peers: Vec<&str> = resp["peers"]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();
        let remaining = if peers.is_empty() {
            "none".to_string()
        } else {
            peers.join(", ")
        };
        println!("Peer '{}' removed. Remaining peers: {}", name, remaining);
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

pub async fn cmd_peer_ping(client: &Client, name: &str) -> Result<(), crate::client::Error> {
    let body = serde_json::json!({"name": name});
    let resp: serde_json::Value = client.post("/peers/ping", &body).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        let h = &resp["health"];
        println!("Peer '{}' is reachable:", name);
        println!(
            "  Version:   {}",
            h.get("version").and_then(|v| v.as_str()).unwrap_or("?")
        );
        println!(
            "  Uptime:    {:.0}s",
            h.get("uptime_s").and_then(|v| v.as_f64()).unwrap_or(0.0)
        );
        println!(
            "  Coherence: {:.4}",
            h.get("mps_coherence")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0)
        );
    } else {
        eprintln!(
            "Peer '{}' unreachable: {}",
            name,
            resp.get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    Ok(())
}

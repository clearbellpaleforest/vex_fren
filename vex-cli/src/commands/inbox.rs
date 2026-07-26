use crate::client::Client;

pub async fn cmd_ask(client: &Client, message: &str) -> Result<(), crate::client::Error> {
    let body = serde_json::json!({"message": message});
    let resp: serde_json::Value = client.post("/ask", &body).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        println!("{}", resp.get("reply").and_then(|v| v.as_str()).unwrap_or("..."));
    } else if let Some(reply) = resp.get("reply").and_then(|v| v.as_str()) {
        println!("{}", reply);
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

pub async fn cmd_inbox(client: &Client) -> Result<(), crate::client::Error> {
    let resp: serde_json::Value = client.post("/poke", &serde_json::json!({})).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        let n = resp["processed"].as_i64().unwrap_or(0);
        let senders: Vec<&str> = resp["senders"]
            .as_array()
            .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
            .unwrap_or_default();
        if n == 0 {
            println!("No new messages.");
        } else {
            println!("Processed {} message(s) from: {}", n, senders.join(", "));
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

pub async fn cmd_poke(_client: &Client, peer_name: &str) -> Result<(), crate::client::Error> {
    // Load peer config from file
    let home = std::env::var("VEX_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            let mut h = crate::client::dirs_fallback();
            h.push("vex");
            h
        });
    let peers_path = home.join("vex_peers.json");
    let peers_cfg: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(&peers_path).unwrap_or_default(),
    )
    .unwrap_or_default();
    let peer = peers_cfg["peers"]
        .get(peer_name)
        .ok_or_else(|| {
            eprintln!("vex: peer '{}' not found", peer_name);
            std::process::exit(1);
        })
        .unwrap();

    let peer_url = peer["url"].as_str().unwrap_or("");
    let _peer_token = peer["token"].as_str().unwrap_or("");

    // Create a client pointing at the peer
    let peer_client = Client::new(peer_url)?;
    let body = serde_json::json!({});
    let resp: serde_json::Value = peer_client.post("/poke", &body).await?;
    if resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        println!(
            "Poked {}: {} messages processed",
            peer_name,
            resp["processed"].as_i64().unwrap_or(0)
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

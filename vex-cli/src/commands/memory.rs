use crate::client::Client;

pub async fn cmd_memory(client: &Client) -> Result<(), crate::client::Error> {
    let resp: serde_json::Value = client.get("/memory/recent").await?;
    if let Some(entries) = resp.as_array() {
        if entries.is_empty() {
            println!("No session memories yet.");
            return Ok(());
        }
        for entry in entries {
            let date = entry["date"].as_str().unwrap_or("unknown");
            let summary = entry["summary"]
                .as_str()
                .or_else(|| {
                    entry["decisions"]
                        .as_array()
                        .and_then(|d| d.first())
                        .and_then(|v| v.as_str())
                })
                .unwrap_or("no summary");
            println!("  {}: {}", date, summary);
        }
    } else if resp.get("error").is_some() {
        eprintln!(
            "Error: {}",
            resp["error"].as_str().unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    Ok(())
}

pub async fn cmd_seed(client: &Client) -> Result<(), crate::client::Error> {
    let text = client.get_text("/seed").await?;
    print!("{}", text);
    Ok(())
}

pub async fn cmd_self(client: &Client) -> Result<(), crate::client::Error> {
    let model: serde_json::Value = client.get("/self").await?;
    let identity = &model["identity"];
    let name = identity["name"].as_str().unwrap_or("Vex");
    let given = identity["given_name"].as_str().unwrap_or("");
    let full = if !given.is_empty() {
        format!("{} {}", name, given)
    } else {
        name.to_string()
    };
    println!("Identity: {}", full);
    println!();

    let caps = &model["capabilities"];
    if let Some(caps_obj) = caps.as_object() {
        if caps_obj.is_empty() {
            println!("No capabilities tracked.");
            return Ok(());
        }
        let mut keys: Vec<&str> = caps_obj.keys().map(|s| s.as_str()).collect();
        keys.sort();
        for cap_name in keys {
            let cap = &caps_obj[cap_name];
            let skill = cap["estimated_skill"].as_f64().unwrap_or(0.0);
            let conf = cap["confidence"].as_f64().unwrap_or(0.0);
            let obs = cap["n_observations"].as_i64().unwrap_or(0);
            let filled = (skill * 20.0) as usize;
            let empty = 20 - filled;
            let bar = format!("{}{}", "█".repeat(filled), "░".repeat(empty));
            println!(
                "  {:25} {} {:.2} ({} obs, {:.0}% conf)",
                cap_name,
                bar,
                skill,
                obs,
                conf * 100.0
            );
        }
    } else {
        println!("No capabilities tracked.");
    }
    Ok(())
}

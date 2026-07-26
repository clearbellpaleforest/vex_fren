use crate::client::Client;

pub async fn cmd_status(client: &Client) -> Result<(), crate::client::Error> {
    let health: serde_json::Value = client.get("/health").await?;

    println!("Vex Daemon v{}", health["version"].as_str().unwrap_or("?"));
    println!(
        "  Uptime:    {:.0}s",
        health["uptime_s"].as_f64().unwrap_or(0.0)
    );
    println!(
        "  Ticks:     {}",
        health["tick_count"].as_i64().unwrap_or(0)
    );
    let last_tick = health["last_tick"].as_str().unwrap_or("never");
    println!("  Last tick: {}", &last_tick[..last_tick.len().min(19)]);
    println!(
        "  Coherence: {:.4}",
        health["mps_coherence"].as_f64().unwrap_or(0.0)
    );
    let drift = health["mps_drift"].as_f64().unwrap_or(0.0);
    let flag = if drift > 0.05 { " ⚠" } else { "" };
    println!("  Drift:     {:.4}{}", drift, flag);
    if let Some(ls) = health["last_session"].as_str() {
        println!("  Last sess: {}", &ls[..ls.len().min(19)]);
    }
    Ok(())
}

pub async fn cmd_health(client: &Client) -> Result<(), crate::client::Error> {
    let health: serde_json::Value = client.get("/health").await?;
    println!("{}", serde_json::to_string_pretty(&health).unwrap());
    Ok(())
}

pub async fn cmd_check(client: &Client) -> Result<(), crate::client::Error> {
    cmd_status(client).await?;
    println!();
    crate::commands::cmd_introspect(client).await?;
    println!("\nProjects:");
    crate::commands::cmd_projects(client).await?;
    Ok(())
}

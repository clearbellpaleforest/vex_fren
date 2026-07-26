use crate::client::Client;

pub async fn cmd_projects(client: &Client) -> Result<(), crate::client::Error> {
    let resp: serde_json::Value = client.get("/projects").await?;
    if !resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        eprintln!(
            "Error: {}",
            resp.get("error").and_then(|v| v.as_str()).unwrap_or("unknown")
        );
        std::process::exit(1);
    }
    let projects = resp.get("projects").and_then(|v| v.as_array());
    if projects.map_or(true, |p| p.is_empty()) {
        println!("No projects found in ~/work.");
        return Ok(());
    }
    for p in projects.unwrap() {
        let name = p["name"].as_str().unwrap_or("?");
        let status = &p["status"];
        let branch = status["branch"].as_str().unwrap_or("?");
        let staged = status["staged"].as_i64().unwrap_or(0);
        let unstaged = status["unstaged"].as_i64().unwrap_or(0);
        let untracked = status["untracked"].as_i64().unwrap_or(0);
        let dirty = if status["dirty"].as_bool().unwrap_or(false) {
            " ⚠"
        } else {
            ""
        };

        let mut parts = Vec::new();
        if staged > 0 {
            parts.push(format!("{} staged", staged));
        }
        if unstaged > 0 {
            parts.push(format!("{} unstaged", unstaged));
        }
        if untracked > 0 {
            parts.push(format!("{} untracked", untracked));
        }
        let detail = if parts.is_empty() {
            "clean".to_string()
        } else {
            parts.join(", ")
        };

        println!("  {:20} {:15} {}{}", name, branch, detail, dirty);
    }
    Ok(())
}

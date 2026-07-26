use crate::client::Client;

pub async fn cmd_export(
    client: &Client,
    output_path: Option<&str>,
) -> Result<(), crate::client::Error> {
    let path = output_path.unwrap_or("vex-bundle.tar.gz");
    client
        .get_binary("/export", std::path::Path::new(path))
        .await?;
    let size_mb = std::fs::metadata(path)
        .map(|m| m.len() as f64 / (1024.0 * 1024.0))
        .unwrap_or(0.0);
    println!("Exported: {} ({:.1} MB)", path, size_mb);
    println!("Transfer this to another machine, then: vex import vex-bundle.tar.gz");
    Ok(())
}

pub async fn cmd_import(client: &Client, bundle_path: &str) -> Result<(), crate::client::Error> {
    if !std::path::Path::new(bundle_path).exists() {
        eprintln!("vex: bundle file not found: {}", bundle_path);
        std::process::exit(1);
    }
    let bytes = std::fs::read(bundle_path).unwrap_or_else(|e| {
        eprintln!("vex: cannot read {}: {}", bundle_path, e);
        std::process::exit(1);
    });

    let resp: serde_json::Value = client
        .post_bytes("/import", bytes, "application/gzip")
        .await?;
    if resp
        .get("ok")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        println!(
            "Imported: {}",
            resp.get("note")
                .and_then(|v| v.as_str())
                .unwrap_or("done")
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

fn load_peer(name: &str) -> (String, String) {
    let home = std::env::var("VEX_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            let mut h = crate::client::dirs_fallback();
            h.push("vex");
            h
        });
    let peers_path = home.join("vex_peers.json");
    let cfg: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(&peers_path).unwrap_or_default(),
    )
    .unwrap_or_default();
    let peer = cfg["peers"].get(name).unwrap_or_else(|| {
        eprintln!("vex: peer '{}' not found", name);
        std::process::exit(1);
    });
    (
        peer["url"].as_str().unwrap_or("").to_string(),
        peer["token"].as_str().unwrap_or("").to_string(),
    )
}

pub async fn cmd_push(client: &Client, peer_name: &str) -> Result<(), crate::client::Error> {
    let (peer_url, peer_token) = load_peer(peer_name);

    // Export from local daemon into memory
    let export_url = format!("{}/export", client.base_url());
    let resp = client.http_get_raw(&export_url).await?;
    let bytes = resp.bytes().await.map_err(crate::client::Error::from)?;

    // Push to peer
    let peer_client = Client::with_token(&peer_url, &peer_token)?;
    let resp: serde_json::Value = peer_client
        .post_bytes("/import", bytes.to_vec(), "application/gzip")
        .await?;
    if resp
        .get("ok")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        println!(
            "Pushed: {} updated. {}",
            peer_name,
            resp.get("note")
                .and_then(|v| v.as_str())
                .unwrap_or("")
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

pub async fn cmd_pull(
    _client: &Client,
    peer_name: &str,
    path: &str,
) -> Result<(), crate::client::Error> {
    let (peer_url, peer_token) = load_peer(peer_name);
    let peer_client = Client::with_token(&peer_url, &peer_token)?;

    let fetch_url = format!(
        "{}/files?path={}",
        peer_client.base_url(),
        url_encode(path)
    );
    let resp = peer_client.http_get_raw(&fetch_url).await?;

    let content_type = resp
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let disposition = resp
        .headers()
        .get("content-disposition")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if content_type.contains("application/gzip") || disposition.contains(".tar.gz") {
        let out_name = path
            .trim_end_matches('/')
            .split('/')
            .last()
            .unwrap_or(path);
        let tar_path = format!("{}.tar.gz", out_name);
        let bytes = resp.bytes().await.map_err(crate::client::Error::from)?;
        let size_mb = bytes.len() as f64 / (1024.0 * 1024.0);
        std::fs::write(&tar_path, &bytes).unwrap_or_else(|e| {
            eprintln!("vex: cannot write {}: {}", tar_path, e);
            std::process::exit(1);
        });
        println!("Pulled: {} ({:.1} MB)", tar_path, size_mb);
        println!("Unpack: tar xzf {}", tar_path);
    } else {
        let out_name = path.split('/').last().unwrap_or(path);
        let text = resp.text().await.map_err(crate::client::Error::from)?;
        std::fs::write(out_name, &text).unwrap_or_else(|e| {
            eprintln!("vex: cannot write {}: {}", out_name, e);
            std::process::exit(1);
        });
        println!("Pulled: {} ({} bytes)", out_name, text.len());
    }
    Ok(())
}

fn url_encode(s: &str) -> String {
    let mut result = String::new();
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
                result.push(byte as char);
            }
            _ => {
                result.push_str(&format!("%{:02X}", byte));
            }
        }
    }
    result
}

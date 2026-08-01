use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use serde::de::DeserializeOwned;
use std::path::PathBuf;

/// Talks to the Vex Daemon over HTTP. Reads the auth token from
/// `~/.vex_token` on construction.
pub struct Client {
    base_url: String,
    token: String,
    http: reqwest::Client,
}

#[derive(Debug)]
pub enum Error {
    TokenNotFound(PathBuf),
    DaemonUnreachable(String),
    Http(reqwest::Error),
    Api { status: u16, body: String },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::TokenNotFound(p) => write!(f, "token not found at {} — is the daemon running?", p.display()),
            Error::DaemonUnreachable(e) => write!(f, "daemon not reachable ({})", e),
            Error::Http(e) => write!(f, "HTTP error: {}", e),
            Error::Api { status, body } => write!(f, "API error {}: {}", status, body),
        }
    }
}

impl From<reqwest::Error> for Error {
    fn from(e: reqwest::Error) -> Self {
        if e.is_connect() || e.is_timeout() {
            Error::DaemonUnreachable(e.to_string())
        } else {
            Error::Http(e)
        }
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::DaemonUnreachable(e.to_string())
    }
}

impl Client {
    /// Create a new client. Reads token from `~/.vex_token`.
    pub fn new(daemon_url: &str) -> Result<Self, Error> {
        let home = std::env::var("VEX_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                // Check current directory first — CLI may be invoked from Vex home
                if let Ok(cwd) = std::env::current_dir() {
                    if cwd.join("vex.db").exists() || cwd.join(".vex_token").exists() {
                        return cwd;
                    }
                }
                let mut h = dirs_fallback();
                h.push("vex");
                h
            });
        let token_path = home.join(".vex_token");
        let token = std::fs::read_to_string(&token_path)
            .map(|s| s.trim().to_string())
            .map_err(|_| Error::TokenNotFound(token_path))?;
        Self::with_token(daemon_url, &token)
    }

    /// Create a client with an explicit token (for peer communication).
    pub fn with_token(daemon_url: &str, token: &str) -> Result<Self, Error> {
        Ok(Client {
            base_url: daemon_url.trim_end_matches('/').to_string(),
            token: token.to_string(),
            http: reqwest::Client::new(),
        })
    }

    /// The daemon's base URL.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Raw GET — returns the response for header/body inspection.
    pub async fn http_get_raw(&self, url: &str) -> Result<reqwest::Response, Error> {
        let resp = self
            .http
            .get(url)
            .headers(self.auth_headers())
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            return Err(Error::Api { status, body });
        }
        Ok(resp)
    }

    fn auth_headers(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        let bearer = format!("Bearer {}", self.token);
        headers.insert(AUTHORIZATION, HeaderValue::from_str(&bearer).unwrap());
        headers
    }

    fn json_headers(&self) -> HeaderMap {
        let mut headers = self.auth_headers();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers
    }

    /// GET a path, returning parsed JSON.
    pub async fn get<T: DeserializeOwned>(&self, path: &str) -> Result<T, Error> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self
            .http
            .get(&url)
            .headers(self.auth_headers())
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            return Err(Error::Api { status, body });
        }
        Ok(resp.json().await?)
    }

    /// GET a path, returning the raw text body.
    pub async fn get_text(&self, path: &str) -> Result<String, Error> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self
            .http
            .get(&url)
            .headers(self.auth_headers())
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            return Err(Error::Api { status, body });
        }
        Ok(resp.text().await?)
    }

    /// POST JSON to a path, returning parsed JSON.
    pub async fn post<T: DeserializeOwned>(
        &self,
        path: &str,
        body: &serde_json::Value,
    ) -> Result<T, Error> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self
            .http
            .post(&url)
            .headers(self.json_headers())
            .json(body)
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            return Err(Error::Api { status, body });
        }
        Ok(resp.json().await?)
    }

    /// GET binary data from a path, writing to a file.
    pub async fn get_binary(&self, path: &str, output: &std::path::Path) -> Result<(), Error> {
        let url = format!("{}{}", self.base_url, path);
        let resp = self
            .http
            .get(&url)
            .headers(self.auth_headers())
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            return Err(Error::Api { status, body });
        }
        let bytes = resp.bytes().await?;
        std::fs::write(output, bytes)?;
        Ok(())
    }

    /// POST raw bytes to a path.
    pub async fn post_bytes<T: DeserializeOwned>(
        &self,
        path: &str,
        body: Vec<u8>,
        content_type: &str,
    ) -> Result<T, Error> {
        let url = format!("{}{}", self.base_url, path);
        let mut headers = self.auth_headers();
        headers.insert(CONTENT_TYPE, HeaderValue::from_str(content_type).unwrap());
        let resp = self
            .http
            .post(&url)
            .headers(headers)
            .body(body)
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let body = resp.text().await.unwrap_or_default();
            return Err(Error::Api { status, body });
        }
        Ok(resp.json().await?)
    }
}

/// Best-effort home directory without pulling in the `dirs` crate.
pub fn dirs_fallback() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

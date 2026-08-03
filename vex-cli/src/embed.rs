//! Embedding generation via Ollama API.
//!
//! Uses all-MiniLM-L6-v2 (384-dim, local, free) to generate semantic
//! embeddings for journal entries and queries. Embeddings are stored
//! in SQLite alongside the text for three-axis semantic recall.
//!
//! Falls back gracefully: if Ollama isn't running or the model isn't
//! pulled, embedding functions return None and the recall engine
//! falls back to FTS5 keyword search.

use serde::{Deserialize, Serialize};

const OLLAMA_URL: &str = "http://localhost:11434/api/embeddings";
const EMBED_MODEL: &str = "all-minilm:latest";
pub const EMBED_DIMS: usize = 384;

#[derive(Debug, Serialize)]
struct EmbedRequest {
    model: String,
    prompt: String,
}

#[derive(Debug, Deserialize)]
struct EmbedResponse {
    embedding: Vec<f32>,
}

/// Check if the embedding service is available.
#[allow(dead_code)]
pub async fn is_available() -> bool {
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };

    let resp = client
        .post(OLLAMA_URL)
        .json(&EmbedRequest {
            model: EMBED_MODEL.to_string(),
            prompt: "test".to_string(),
        })
        .send()
        .await;

    match resp {
        Ok(r) => r.status().is_success(),
        Err(_) => false,
    }
}

/// Generate an embedding for a piece of text.
/// Returns None if the embedding service is unavailable.
pub async fn embed_text(text: &str) -> Option<Vec<f32>> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .ok()?;

    let resp = client
        .post(OLLAMA_URL)
        .json(&EmbedRequest {
            model: EMBED_MODEL.to_string(),
            prompt: text.to_string(),
        })
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        return None;
    }

    let data: EmbedResponse = resp.json().await.ok()?;

    if data.embedding.len() != EMBED_DIMS {
        return None;
    }

    Some(data.embedding)
}

/// Cosine similarity between two vectors.
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    if a.len() != b.len() {
        return 0.0;
    }

    let dot: f64 = a.iter().zip(b.iter()).map(|(x, y)| (*x as f64) * (*y as f64)).sum();
    let norm_a: f64 = a.iter().map(|x| (*x as f64) * (*x as f64)).sum::<f64>().sqrt();
    let norm_b: f64 = b.iter().map(|x| (*x as f64) * (*x as f64)).sum::<f64>().sqrt();

    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }

    dot / (norm_a * norm_b)
}

/// Store an embedding vector as a JSON string in SQLite.
pub fn encode_embedding(vec: &[f32]) -> String {
    serde_json::to_string(vec).unwrap_or_else(|_| "[]".to_string())
}

/// Decode an embedding vector from a JSON string in SQLite.
pub fn decode_embedding(json: &str) -> Option<Vec<f32>> {
    serde_json::from_str(json).ok()
}

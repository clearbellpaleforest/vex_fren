//! Three-axis semantic recall engine.
//!
//! Scores every memory entry on three axes:
//!   1. Semantic similarity (embedding cosine distance)
//!   2. Temporal relevance (how close to the queried time period)
//!   3. Emotional salience (does the memory's emotion match the query)
//!
//! Falls back to FTS5 keyword search when embeddings are unavailable.

use crate::embed::{self, cosine_similarity, decode_embedding, EMBED_DIMS};
use crate::emotion::{query_emotions, Emotion};
use crate::temporal::{DateRange, parse_temporal};
use serde::{Deserialize, Serialize};


/// A scored memory entry returned by recall.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecallResult {
    pub date: String,
    pub summary: String,
    pub entry: String,
    pub score: f64,
    pub emotion: String,
    pub matched_emotion: bool,
    pub in_time_range: bool,
}

/// Weights for the three-axis scoring function.
#[derive(Debug, Clone)]
pub struct RecallWeights {
    pub semantic: f64,  // default 0.50
    pub temporal: f64,  // default 0.30
    pub emotional: f64, // default 0.20
}

impl Default for RecallWeights {
    fn default() -> Self {
        Self {
            semantic: 0.50,
            temporal: 0.30,
            emotional: 0.20,
        }
    }
}

/// Score a single memory entry against a query.
///
/// - `query_embedding`: the embedding of the user's query (None if unavailable)
/// - `memory_embedding`: the stored embedding of the memory entry (None if not yet generated)
/// - `memory_date`: the date of the memory entry
/// - `time_range`: the parsed temporal range from the query (None if no time expression)
/// - `memory_emotion`: the emotion tag of the memory entry
/// - `query_emotions`: emotions detected in the user's query
/// - `weights`: scoring weights
fn score_entry(
    query_embedding: Option<&[f32]>,
    memory_embedding: Option<&[f32]>,
    memory_date: &str,
    time_range: &Option<DateRange>,
    memory_emotion: Emotion,
    query_emotions: &[Emotion],
    weights: &RecallWeights,
) -> f64 {
    let mut score = 0.0;

    // Axis 1: Semantic similarity
    if let (Some(q_emb), Some(m_emb)) = (query_embedding, memory_embedding) {
        score += weights.semantic * cosine_similarity(q_emb, m_emb).max(0.0);
    }

    // Axis 2: Temporal relevance
    if let Some(range) = time_range {
        if let Ok(date) = chrono::NaiveDate::parse_from_str(memory_date, "%Y-%m-%d") {
            if date >= range.start && date <= range.end {
                score += weights.temporal * 1.0; // Full temporal bonus
            } else {
                // Partial bonus: closer = better, tapered over 30 days
                let days_off = if date < range.start {
                    (range.start - date).num_days() as f64
                } else {
                    (date - range.end).num_days() as f64
                };
                let temporal_boost = (1.0 - (days_off / 30.0).min(1.0)).max(0.0);
                score += weights.temporal * temporal_boost;
            }
        }
    }

    // Axis 3: Emotional salience
    if query_emotions.iter().any(|e| *e == memory_emotion) {
        score += weights.emotional * memory_emotion.query_boost();
    } else if query_emotions.contains(&Emotion::Neutral) {
        // No emotional query → small flat bonus for emotionally tagged memories
        if memory_emotion != Emotion::Neutral {
            score += weights.emotional * 0.3;
        }
    }

    score
}

/// Perform a three-axis semantic recall.
///
/// `candidates` should be pre-filtered from FTS5 (wide net, top-200).
/// Each candidate is (date, summary, full_text, stored_embedding_json, emotion_tag).
pub async fn recall(
    query: &str,
    candidates: Vec<(String, String, String, Option<String>, String)>,
) -> Vec<RecallResult> {
    let weights = RecallWeights::default();

    // Parse temporal expression from query
    let time_range = parse_temporal(query);

    // Detect emotions in query
    let q_emotions = query_emotions(query);

    // Generate query embedding
    let query_embedding = embed::embed_text(query).await;

    let mut results: Vec<RecallResult> = Vec::new();

    for (date, summary, entry, stored_emb_json, emotion_str) in candidates {
        let memory_emotion: Emotion = emotion_str.parse().unwrap_or(Emotion::Neutral);
        let memory_embedding = stored_emb_json
            .as_deref()
            .and_then(decode_embedding);

        let score = score_entry(
            query_embedding.as_deref(),
            memory_embedding.as_deref(),
            &date,
            &time_range,
            memory_emotion,
            &q_emotions,
            &weights,
        );

        let matched_emotion = q_emotions.contains(&memory_emotion);
        let in_time_range = time_range.as_ref().map_or(false, |range| {
            chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                .map(|d| d >= range.start && d <= range.end)
                .unwrap_or(false)
        });

        results.push(RecallResult {
            date,
            summary,
            entry,
            score,
            emotion: memory_emotion.as_str().to_string(),
            matched_emotion,
            in_time_range,
        });
    }

    // Sort by score descending, then by date descending for ties
    results.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.date.cmp(&a.date))
    });

    results
}

/// Legacy keyword-only fallback. Used when embeddings are unavailable.
/// Ranks by FTS5 match count + temporal filter + recency.
pub fn keyword_recall(
    query: &str,
    candidates: Vec<(String, String, String, Option<String>, String)>,
) -> Vec<RecallResult> {
    let time_range = parse_temporal(query);
    let q_emotions = query_emotions(query);
    let query_lower = query.to_lowercase();
    let query_words: Vec<&str> = query_lower.split_whitespace().collect();

    let mut results: Vec<RecallResult> = Vec::new();

    for (date, summary, entry, _stored_emb, emotion_str) in candidates {
        let memory_emotion: Emotion = emotion_str.parse().unwrap_or(Emotion::Neutral);

        // Keyword match count
        let lower_entry = entry.to_lowercase();
        let match_count: usize = query_words
            .iter()
            .filter(|w| lower_entry.contains(**w))
            .count();
        let keyword_score = if query_words.is_empty() {
            0.0
        } else {
            match_count as f64 / query_words.len() as f64
        };

        // Temporal bonus
        let temporal_score = time_range.as_ref().map_or(0.0, |range| {
            chrono::NaiveDate::parse_from_str(&date, "%Y-%m-%d")
                .map(|d| {
                    if d >= range.start && d <= range.end {
                        1.0
                    } else {
                        0.0
                    }
                })
                .unwrap_or(0.0)
        });

        // Emotional bonus
        let emotion_score = if q_emotions.contains(&memory_emotion) {
            memory_emotion.query_boost() * 0.2
        } else {
            0.0
        };

        let score = keyword_score * 0.6 + temporal_score * 0.3 + emotion_score * 0.1;
        let matched_emotion = q_emotions.contains(&memory_emotion);
        let in_time_range = temporal_score > 0.0;

        results.push(RecallResult {
            date,
            summary,
            entry,
            score,
            emotion: memory_emotion.as_str().to_string(),
            matched_emotion,
            in_time_range,
        });
    }

    results.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.date.cmp(&a.date))
    });

    results
}

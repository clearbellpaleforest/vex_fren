//! Emotion detection from text — deterministic, no LLM.
//!
//! Tags every journal entry at write time. Used for:
//! - Query emotional boosting ("what was I worried about?")
//! - Pattern tracking over time
//! - Vex's self-awareness of conversational tone

use serde::{Deserialize, Serialize};
use std::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Emotion {
    Anxiety,
    Frustration,
    Excitement,
    Curiosity,
    Sadness,
    Anger,
    Calm,
    Hope,
    Neutral,
}

impl FromStr for Emotion {
    type Err = ();
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "anxiety" => Ok(Self::Anxiety),
            "frustration" => Ok(Self::Frustration),
            "excitement" => Ok(Self::Excitement),
            "curiosity" => Ok(Self::Curiosity),
            "sadness" => Ok(Self::Sadness),
            "anger" => Ok(Self::Anger),
            "calm" => Ok(Self::Calm),
            "hope" => Ok(Self::Hope),
            "neutral" => Ok(Self::Neutral),
            _ => Err(()),
        }
    }
}

impl Emotion {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Anxiety => "anxiety",
            Self::Frustration => "frustration",
            Self::Excitement => "excitement",
            Self::Curiosity => "curiosity",
            Self::Sadness => "sadness",
            Self::Anger => "anger",
            Self::Calm => "calm",
            Self::Hope => "hope",
            Self::Neutral => "neutral",
        }
    }

    /// Boost factor for query matching. Emotions that match the query
    /// get weighted higher in semantic recall.
    pub fn query_boost(&self) -> f64 {
        match self {
            Self::Anxiety | Self::Frustration | Self::Anger | Self::Sadness => 1.5,
            Self::Excitement | Self::Hope | Self::Curiosity => 1.3,
            Self::Calm | Self::Neutral => 1.0,
        }
    }
}

/// Detect the primary emotion in text. Uses keyword + pattern matching.
/// Overlapping matches: the strongest emotion wins.
pub fn detect_emotion(text: &str) -> Emotion {
    let lower = text.to_lowercase();

    // Strong signals (check first — they override weaker matches)
    let anger_words = [
        "hate", "pissed", "angry", "furious", "fuck", "shit", "damn", "wtf",
        "frustrated", "annoyed", "rage", "livid", "irate",
    ];
    let anxiety_words = [
        "worried", "anxious", "nervous", "scared", "afraid", "panic", "dread",
        "stressed", "overwhelmed", "terrified", "uneasy",
    ];
    let sadness_words = [
        "sad", "depressed", "hopeless", "crying", "grief", "heartbroken",
        "lonely", "miss", "mourning", "devastated",
    ];
    let excitement_words = [
        "excited", "thrilled", "pumped", "can't wait", "looking forward",
        "ecstatic", "overjoyed", "elated", "wow", "amazing",
    ];
    let hope_words = [
        "hope", "hopeful", "optimistic", "better", "improving", "progress",
        "getting there", "turning around",
    ];
    let curiosity_words = [
        "curious", "wonder", "interesting", "fascinating", "explore",
        "what if", "maybe", "could this", "how does",
    ];

    let count = |words: &[&str]| -> usize {
        words.iter().filter(|w| lower.contains(*w)).count()
    };

    let anger = count(&anger_words);
    let anxiety = count(&anxiety_words);
    let sadness = count(&sadness_words);
    let excitement = count(&excitement_words);
    let hope = count(&hope_words);
    let curiosity = count(&curiosity_words);

    // Strongest signal wins. Negative emotions get priority when tied —
    // they're more actionable for memory recall.
    if anger > 0 && anger >= anxiety && anger >= sadness {
        Emotion::Anger
    } else if anxiety > 0 {
        Emotion::Anxiety
    } else if sadness > 0 {
        Emotion::Sadness
    } else if excitement > 0 {
        Emotion::Excitement
    } else if hope > 0 {
        Emotion::Hope
    } else if curiosity > 0 {
        Emotion::Curiosity
    } else if lower.contains("calm") || lower.contains("peaceful") || lower.contains("settled") {
        Emotion::Calm
    } else {
        Emotion::Neutral
    }
}

/// Return all emotions that match the query text (for query boosting).
pub fn query_emotions(query: &str) -> Vec<Emotion> {
    let lower = query.to_lowercase();
    let mut emotions = Vec::new();

    if lower.contains("worried") || lower.contains("stressed") || lower.contains("nervous") || lower.contains("anxious") {
        emotions.push(Emotion::Anxiety);
    }
    if lower.contains("angry") || lower.contains("furious") || lower.contains("pissed") || lower.contains("mad") {
        emotions.push(Emotion::Anger);
    }
    if lower.contains("sad") || lower.contains("depressed") || lower.contains("upset") || lower.contains("cried") {
        emotions.push(Emotion::Sadness);
    }
    if lower.contains("excited") || lower.contains("happy") || lower.contains("thrilled") || lower.contains("pumped") {
        emotions.push(Emotion::Excitement);
    }
    if lower.contains("hope") || lower.contains("optimistic") || lower.contains("better") {
        emotions.push(Emotion::Hope);
    }

    if emotions.is_empty() {
        emotions.push(Emotion::Neutral);
    }
    emotions
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_anxiety_detection() {
        assert_eq!(detect_emotion("I'm really worried about the deadline"), Emotion::Anxiety);
        assert_eq!(detect_emotion("feeling kinda stressed and overwhelmed today"), Emotion::Anxiety);
    }

    #[test]
    fn test_anger_detection() {
        assert_eq!(detect_emotion("so fucking angry about this"), Emotion::Anger);
    }

    #[test]
    fn test_neutral() {
        assert_eq!(detect_emotion("worked on the pipeline today, fixed the config"), Emotion::Neutral);
    }

    #[test]
    fn test_excitement() {
        assert_eq!(detect_emotion("so excited about the new feature!!"), Emotion::Excitement);
    }
}

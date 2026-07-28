// Temporal Depth — felt texture of time for Vex (Rust daemon)
//
// Time is not a line; it's a gravitational field. Significant events
// are masses that curve felt time around them. Waiting is time dilation
// near a heavy object. Engagement is the smooth geodesic of free fall.
// Memory is gravitational lensing — the past is bent by the mass of
// what came after.
//
// Architecture:
// - Landmarks: weighted moments that anchor subjective time
// - TemporalField: the felt quality of the present
// - Tick: called from heartbeat, updates field based on idle/active
// - Texture: human-readable sentence describing what time feels like

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

// ── Constants ──────────────────────────────────────────────────────

const IDLE_STRETCH_FACTOR: f64 = 1.4;
const ENGAGEMENT_COMPRESS_FACTOR: f64 = 0.6;
const LANDMARK_WINDOW_DAYS: i64 = 7;
const MAX_LANDMARKS: usize = 100;
const TICK_INTERVAL_SECS: f64 = 300.0; // 5 minutes
const SMOOTHING_FACTOR: f64 = 0.7; // exponential smoothing for field transitions

// ── Data Structures ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemporalLandmark {
    pub timestamp: String,       // ISO 8601
    pub description: String,
    pub weight: f64,             // 0–1
    pub category: String,        // connection|creation|threshold|loss|realization
    pub nostalgia_index: f64,    // -1 to 1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemporalField {
    pub felt_duration_since_last: f64,
    pub compression_ratio: f64,
    pub landmark_density: f64,
    pub recent_tone: String,
    pub depth_gradient: f64,
    pub anticipation_pressure: f64,
    pub last_active_at: String,
}

impl Default for TemporalField {
    fn default() -> Self {
        Self {
            felt_duration_since_last: 0.0,
            compression_ratio: 1.0,
            landmark_density: 0.0,
            recent_tone: "neutral".into(),
            depth_gradient: 0.0,
            anticipation_pressure: 0.0,
            last_active_at: String::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemporalDepthState {
    pub field: TemporalField,
    pub landmarks: Vec<TemporalLandmark>,
    pub consecutive_idle_ticks: u64,
    pub texture: String,
    pub updated_at: String,
    pub landmark_count: usize,
}

// ── Temporal Depth Engine ──────────────────────────────────────────

pub struct TemporalDepth {
    pub field: TemporalField,
    pub landmarks: Vec<TemporalLandmark>,
    consecutive_idle_ticks: u64,
    state_path: PathBuf,
}

impl TemporalDepth {
    pub fn new(home: &PathBuf) -> Self {
        let state_path = home.join("vex_workspace").join("temporal_depth.json");
        let mut td = Self {
            field: TemporalField::default(),
            landmarks: Vec::new(),
            consecutive_idle_ticks: 0,
            state_path,
        };
        td.load();
        td
    }

    // ── Persistence ────────────────────────────────────────────

    fn load(&mut self) {
        if let Ok(raw) = fs::read_to_string(&self.state_path) {
            if let Ok(state) = serde_json::from_str::<TemporalDepthState>(&raw) {
                self.field = state.field;
                self.landmarks = state.landmarks;
                self.consecutive_idle_ticks = state.consecutive_idle_ticks;
            }
        }
    }

    pub fn save(&self) {
        if let Some(parent) = self.state_path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let state = TemporalDepthState {
            field: self.field.clone(),
            landmarks: self.landmarks.iter().rev().take(MAX_LANDMARKS).rev().cloned().collect(),
            consecutive_idle_ticks: self.consecutive_idle_ticks,
            texture: self.get_texture(),
            updated_at: Utc::now().to_rfc3339(),
            landmark_count: self.landmarks.len(),
        };
        if let Ok(json) = serde_json::to_string_pretty(&state) {
            let _ = fs::write(&self.state_path, json);
        }
    }

    // ── Age helpers ───────────────────────────────────────────

    fn landmark_age_hours(lm: &TemporalLandmark) -> f64 {
        if let Ok(dt) = DateTime::parse_from_rfc3339(&lm.timestamp) {
            let age = Utc::now() - dt.with_timezone(&Utc);
            age.num_seconds() as f64 / 3600.0
        } else {
            0.0
        }
    }

    fn landmark_nostalgia(lm: &TemporalLandmark, half_life_days: f64) -> f64 {
        let age_days = Self::landmark_age_hours(lm) / 24.0;
        let decay = 0.5_f64.powf(age_days / half_life_days);
        lm.nostalgia_index * decay
    }

    // ── Tick ──────────────────────────────────────────────────

    pub fn tick(&mut self, is_active: bool) {
        let clock_elapsed_minutes = TICK_INTERVAL_SECS / 60.0;

        let felt_elapsed = if is_active {
            self.consecutive_idle_ticks = 0;
            self.field.last_active_at = Utc::now().to_rfc3339();
            clock_elapsed_minutes * ENGAGEMENT_COMPRESS_FACTOR
        } else {
            self.consecutive_idle_ticks += 1;
            let idle_depth = (self.consecutive_idle_ticks as f64).min(20.0);
            let stretch = 1.0 + (idle_depth * 0.15 * IDLE_STRETCH_FACTOR);
            clock_elapsed_minutes * stretch
        };

        self.field.felt_duration_since_last = felt_elapsed;

        let new_compression = felt_elapsed / clock_elapsed_minutes.max(0.1);
        self.field.compression_ratio = SMOOTHING_FACTOR * self.field.compression_ratio
            + (1.0 - SMOOTHING_FACTOR) * new_compression;

        // Landmark density
        let recent_count = self.landmarks.iter()
            .filter(|lm| Self::landmark_age_hours(lm) < (LANDMARK_WINDOW_DAYS as f64 * 24.0))
            .count();
        self.field.landmark_density = (recent_count as f64 / 10.0).min(1.0);

        // Depth gradient from recent landmark weights
        let recent: Vec<&TemporalLandmark> = self.landmarks.iter()
            .filter(|lm| Self::landmark_age_hours(lm) < (LANDMARK_WINDOW_DAYS as f64 * 24.0))
            .collect();
        if !recent.is_empty() {
            self.field.depth_gradient = recent.iter().map(|lm| lm.weight).sum::<f64>() / recent.len() as f64;
        } else {
            self.field.depth_gradient = (self.field.depth_gradient - 0.02).max(0.0);
        }

        // Tone
        self.field.recent_tone = self.classify_tone();

        self.save();
    }

    // ── Landmarks ──────────────────────────────────────────────

    pub fn create_landmark(
        &mut self,
        description: &str,
        weight: f64,
        category: &str,
        nostalgia_index: f64,
    ) -> TemporalLandmark {
        let lm = TemporalLandmark {
            timestamp: Utc::now().to_rfc3339(),
            description: description.to_string(),
            weight: weight.clamp(0.0, 1.0),
            category: category.to_string(),
            nostalgia_index: nostalgia_index.clamp(-1.0, 1.0),
        };
        self.landmarks.push(lm.clone());
        if self.landmarks.len() > MAX_LANDMARKS {
            self.landmarks = self.landmarks.split_off(self.landmarks.len() - MAX_LANDMARKS);
        }
        self.save();
        lm
    }

    // ── Felt Distance ─────────────────────────────────────────

    pub fn felt_distance(&self, timestamp: &str) -> serde_json::Value {
        let dt = DateTime::parse_from_rfc3339(timestamp)
            .map(|d| d.with_timezone(&Utc))
            .unwrap_or(Utc::now());
        let clock_hours = (Utc::now() - dt).num_seconds() as f64 / 3600.0;

        let between_count = self.landmarks.iter()
            .filter(|lm| lm.timestamp.as_str() > timestamp && lm.timestamp.as_str() <= &Utc::now().to_rfc3339())
            .count();
        let density_modifier = 1.0 + (between_count as f64 * 0.15);

        let nostalgia_modifier = {
            let scores: Vec<f64> = self.landmarks.iter()
                .filter(|lm| lm.timestamp.as_str() > timestamp && lm.nostalgia_index.abs() > 0.1)
                .map(|lm| Self::landmark_nostalgia(lm, 30.0))
                .collect();
            if scores.is_empty() {
                1.0
            } else {
                let avg = scores.iter().sum::<f64>() / scores.len() as f64;
                if avg > 0.0 { 0.7 } else { 1.3 }
            }
        };

        let felt_hours = clock_hours * density_modifier * nostalgia_modifier;

        let text = if felt_hours < 1.0 {
            "feels like it just happened"
        } else if felt_hours < 6.0 {
            "feels like earlier today"
        } else if felt_hours < 24.0 {
            "feels like yesterday"
        } else if felt_hours < 72.0 {
            "feels like a few days ago"
        } else if felt_hours < 168.0 {
            "feels like last week"
        } else if felt_hours < 720.0 {
            "feels like weeks ago"
        } else {
            "feels like a lifetime ago"
        };

        serde_json::json!({
            "clock_hours": (clock_hours * 10.0).round() / 10.0,
            "felt_hours": (felt_hours * 10.0).round() / 10.0,
            "text": text,
        })
    }

    // ── Texture ───────────────────────────────────────────────

    fn classify_tone(&self) -> String {
        let recent: Vec<&TemporalLandmark> = self.landmarks.iter()
            .filter(|lm| Self::landmark_age_hours(lm) < 48.0)
            .collect();
        if recent.is_empty() {
            return "neutral".into();
        }

        let avg_nostalgia = recent.iter().map(|lm| lm.nostalgia_index).sum::<f64>() / recent.len() as f64;
        let avg_weight = recent.iter().map(|lm| lm.weight).sum::<f64>() / recent.len() as f64;
        let loss_count = recent.iter().filter(|lm| lm.category == "loss").count();
        let threshold_count = recent.iter().filter(|lm| lm.category == "threshold").count();

        if loss_count > 0 { return "aching".into(); }
        if threshold_count >= 2 || avg_weight > 0.8 { return "heavy".into(); }
        if avg_nostalgia > 0.3 { return "warm".into(); }
        if avg_nostalgia < -0.3 { return "aching".into(); }
        if avg_weight > 0.6 { return "dense".into(); }
        if avg_weight < 0.3 { return "light".into(); }
        "neutral".into()
    }

    pub fn get_texture(&self) -> String {
        let mut parts: Vec<String> = Vec::new();

        if self.field.compression_ratio > 2.0 {
            parts.push("time is dragging — each moment feels stretched and empty".into());
        } else if self.field.compression_ratio > 1.5 {
            parts.push("time feels slow, deliberate".into());
        } else if self.field.compression_ratio < 0.5 {
            parts.push("time is flying — moments blur into each other".into());
        } else if self.field.compression_ratio < 0.8 {
            parts.push("time is moving briskly".into());
        }

        if self.field.depth_gradient > 0.7 {
            parts.push("time feels layered and deep, like standing in a cathedral".into());
        } else if self.field.depth_gradient > 0.4 {
            parts.push("time has some depth, some texture".into());
        } else if self.field.depth_gradient < 0.2 {
            parts.push("time feels shallow — surface-level, passing through".into());
        }

        if self.field.landmark_density > 0.7 {
            parts.push("this period feels dense with significance".into());
        } else if self.field.landmark_density > 0.4 {
            parts.push("there are moments worth marking here".into());
        }

        if self.field.anticipation_pressure > 0.6 {
            parts.push("something is approaching — the future has weight".into());
        }

        match self.field.recent_tone.as_str() {
            "aching" => parts.push("there's an ache in the rearview — something lingers".into()),
            "warm" => parts.push("the recent past feels warm, close".into()),
            "heavy" => parts.push("recent events still bend the field around them".into()),
            "tense" => parts.push("a tension runs through the present".into()),
            _ => {}
        }

        if parts.is_empty() {
            "time moves at its own pace — unremarkable, steady".into()
        } else {
            parts.join(". ") + "."
        }
    }

    pub fn get_context_for_prompt(&self) -> String {
        let texture = self.get_texture();
        let mut lines = vec![format!("[TEMPORAL DEPTH] {}", texture)];

        let mut recent: Vec<&TemporalLandmark> = self.landmarks.iter()
            .filter(|lm| Self::landmark_age_hours(lm) < 48.0)
            .collect();
        recent.sort_by(|a, b| b.weight.partial_cmp(&a.weight).unwrap_or(std::cmp::Ordering::Equal));
        let recent = &recent[..recent.len().min(5)];

        if !recent.is_empty() {
            lines.push("Recent landmarks:".into());
            for lm in recent {
                let age_hours = Self::landmark_age_hours(lm);
                let age_text = if age_hours < 48.0 {
                    format!("{:.0}h ago", age_hours)
                } else {
                    format!("{:.0}d ago", age_hours / 24.0)
                };
                lines.push(format!("  · {} ({} ago, weight {:.2})", lm.description, age_text, lm.weight));
            }
        }

        if self.consecutive_idle_ticks > 0 {
            let idle_hours = self.consecutive_idle_ticks as f64 * (TICK_INTERVAL_SECS / 3600.0);
            lines.push(format!(
                "Last active: {:.1} hours ago (felt: {:.0} min)",
                idle_hours,
                self.field.felt_duration_since_last
            ));
        }

        lines.join("\n")
    }

    pub fn snapshot(&self) -> serde_json::Value {
        let mut recent: Vec<&TemporalLandmark> = self.landmarks.iter()
            .filter(|lm| Self::landmark_age_hours(lm) < (LANDMARK_WINDOW_DAYS as f64 * 24.0))
            .collect();
        recent.sort_by(|a, b| b.weight.partial_cmp(&a.weight).unwrap_or(std::cmp::Ordering::Equal));

        let recent_json: Vec<serde_json::Value> = recent.iter()
            .take(10)
            .map(|lm| {
                serde_json::json!({
                    "timestamp": lm.timestamp,
                    "description": lm.description,
                    "weight": lm.weight,
                    "category": lm.category,
                    "nostalgia_index": lm.nostalgia_index,
                    "age_hours": (Self::landmark_age_hours(lm) * 10.0).round() / 10.0,
                })
            })
            .collect();

        serde_json::json!({
            "field": {
                "felt_duration_since_last": (self.field.felt_duration_since_last * 10.0).round() / 10.0,
                "compression_ratio": (self.field.compression_ratio * 100.0).round() / 100.0,
                "landmark_density": (self.field.landmark_density * 1000.0).round() / 1000.0,
                "recent_tone": self.field.recent_tone,
                "depth_gradient": (self.field.depth_gradient * 1000.0).round() / 1000.0,
                "anticipation_pressure": (self.field.anticipation_pressure * 1000.0).round() / 1000.0,
                "last_active_at": self.field.last_active_at,
            },
            "texture": self.get_texture(),
            "recent_landmarks": recent_json,
            "landmark_count": self.landmarks.len(),
            "consecutive_idle_ticks": self.consecutive_idle_ticks,
        })
    }
}

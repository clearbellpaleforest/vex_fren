//! Temporal query parsing — natural language → date range.
//!
//! Deterministic. No LLM. Handles the common cases people actually use
//! when asking about their own memories.

use chrono::{Datelike, NaiveDate, Utc};

/// A date range for filtering memories.
#[derive(Debug, Clone)]
pub struct DateRange {
    pub start: NaiveDate,
    pub end: NaiveDate,
}

impl DateRange {
    pub fn new(start: NaiveDate, end: NaiveDate) -> Self {
        Self { start, end }
    }
}

/// Parse natural language temporal expressions into date ranges.
/// Returns None if no temporal expression is detected (search all time).
pub fn parse_temporal(query: &str) -> Option<DateRange> {
    let lower = query.to_lowercase();
    let today = Utc::now().date_naive();

    // "last summer" → June–August of previous year
    if lower.contains("last summer") {
        let year = if today.month() >= 6 { today.year() } else { today.year() - 1 };
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(year - 1, 6, 1).unwrap(),
            NaiveDate::from_ymd_opt(year - 1, 8, 31).unwrap(),
        ));
    }

    // "this summer"
    if lower.contains("this summer") {
        let year = today.year();
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(year, 6, 1).unwrap(),
            NaiveDate::from_ymd_opt(year, 8, 31).unwrap(),
        ));
    }

    // "last winter"
    if lower.contains("last winter") {
        let year = today.year();
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(year - 1, 12, 1).unwrap(),
            NaiveDate::from_ymd_opt(year, 2, 28).unwrap(),
        ));
    }

    // "last spring"
    if lower.contains("last spring") {
        let year = today.year();
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(year, 3, 1).unwrap(),
            NaiveDate::from_ymd_opt(year, 5, 31).unwrap(),
        ));
    }

    // "last fall" / "last autumn"
    if lower.contains("last fall") || lower.contains("last autumn") {
        let year = today.year();
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(year - 1, 9, 1).unwrap(),
            NaiveDate::from_ymd_opt(year - 1, 11, 30).unwrap(),
        ));
    }

    // "recently" / "lately" → last 7 days
    if lower.contains("recently") || lower.contains("lately") {
        return Some(DateRange::new(
            today - chrono::Duration::days(7),
            today,
        ));
    }

    // "this week"
    if lower.contains("this week") {
        let weekday = today.weekday().num_days_from_monday() as i64;
        let monday = today - chrono::Duration::days(weekday);
        return Some(DateRange::new(monday, today));
    }

    // "this month"
    if lower.contains("this month") {
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(today.year(), today.month(), 1).unwrap(),
            today,
        ));
    }

    // "last month"
    if lower.contains("last month") {
        let (year, month) = if today.month() == 1 {
            (today.year() - 1, 12)
        } else {
            (today.year(), today.month() - 1)
        };
        let last_day = NaiveDate::from_ymd_opt(year, month + 1, 1)
            .unwrap_or(NaiveDate::from_ymd_opt(year, month, 28).unwrap())
            .pred_opt()
            .unwrap();
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(year, month, 1).unwrap(),
            last_day,
        ));
    }

    // "last year" / "past year"
    if lower.contains("last year") || lower.contains("past year") {
        return Some(DateRange::new(
            NaiveDate::from_ymd_opt(today.year() - 1, 1, 1).unwrap(),
            NaiveDate::from_ymd_opt(today.year() - 1, 12, 31).unwrap(),
        ));
    }

    // "in <month>" → that month in current year (or last year if future)
    let months = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ];
    for (i, name) in months.iter().enumerate() {
        let pattern = format!("in {}", name);
        if lower.contains(&pattern) {
            let month = (i + 1) as u32;
            let year = if month > today.month() { today.year() - 1 } else { today.year() };
            let last_day = NaiveDate::from_ymd_opt(year, month + 1, 1)
                .unwrap_or(NaiveDate::from_ymd_opt(year + 1, 1, 1).unwrap())
                .pred_opt()
                .unwrap();
            return Some(DateRange::new(
                NaiveDate::from_ymd_opt(year, month, 1).unwrap(),
                last_day,
            ));
        }
    }

    // "<month> <year>" e.g. "july 2025"
    for (i, name) in months.iter().enumerate() {
        if lower.contains(name) {
            // Look for a year nearby
            for word in lower.split_whitespace() {
                if let Ok(year) = word.parse::<i32>() {
                    if year >= 2020 && year <= 2030 {
                        let month = (i + 1) as u32;
                        let last_day = NaiveDate::from_ymd_opt(year, month + 1, 1)
                            .unwrap_or(NaiveDate::from_ymd_opt(year + 1, 1, 1).unwrap())
                            .pred_opt()
                            .unwrap();
                        return Some(DateRange::new(
                            NaiveDate::from_ymd_opt(year, month, 1).unwrap(),
                            last_day,
                        ));
                    }
                }
            }
        }
    }

    // "yesterday"
    if lower.contains("yesterday") {
        let yesterday = today - chrono::Duration::days(1);
        return Some(DateRange::new(yesterday, yesterday));
    }

    // "today"
    if lower.contains("today") {
        return Some(DateRange::new(today, today));
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_last_summer() {
        let range = parse_temporal("what was I worried about last summer?").unwrap();
        // Summer = June–August. "Last summer" = previous year.
        assert_eq!(range.start.month(), 6);
        assert_eq!(range.end.month(), 8);
    }

    #[test]
    fn test_recently() {
        let range = parse_temporal("what happened recently").unwrap();
        let today = Utc::now().date_naive();
        assert_eq!(range.end, today);
        assert!(range.start <= today);
    }

    #[test]
    fn test_no_temporal() {
        assert!(parse_temporal("what was I thinking about the pipeline").is_none());
    }

    #[test]
    fn test_in_month() {
        let range = parse_temporal("what did I do in june").unwrap();
        assert_eq!(range.start.month(), 6);
    }
}

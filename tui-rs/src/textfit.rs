//! Display-width aware text fitting for the modal pickers.
//!
//! Terminal columns are not equal to `char` counts: emoji (and other wide
//! glyphs) occupy two cells. Every picker row is laid out from fixed-width
//! columns, so measuring with `str::len()`/`chars().count()` and formatting
//! with `{:<width$}` (which pads by character count) makes rows come out
//! ragged. These helpers measure and pad by *display width* instead, so every
//! row ends on exactly the same column.

use unicode_width::UnicodeWidthStr;

/// Terminal cell width of `s`.
pub fn width(s: &str) -> usize {
    UnicodeWidthStr::width(s)
}

/// Longest prefix of `s` that fits in `budget` cells (no ellipsis).
///
/// Prefixes are measured as a whole with `UnicodeWidthStr` because a lone
/// variation selector (`U+FE0F`) upgrades the preceding glyph to double width;
/// summing per-character widths would miss that.
fn take_cells(s: &str, budget: usize) -> String {
    let mut end_ok = 0;
    for (idx, c) in s.char_indices() {
        let end = idx + c.len_utf8();
        if UnicodeWidthStr::width(&s[..end]) > budget {
            break;
        }
        end_ok = end;
    }
    s[..end_ok].to_string()
}

/// `s` clipped so it is never wider than `max` cells, with a trailing `...`
/// when the text had to be clipped.
pub fn truncate(s: &str, max: usize) -> String {
    if width(s) <= max {
        return s.to_string();
    }
    if max <= 3 {
        // No room for an ellipsis: hard-clip instead.
        return take_cells(s, max);
    }
    let mut out = take_cells(s, max - 3);
    out.push_str("...");
    out
}

/// `s` clipped and padded so the result occupies exactly `max` cells.
pub fn fit(s: &str, max: usize) -> String {
    let mut out = truncate(s, max);
    let used = width(&out);
    if used < max {
        out.push_str(&" ".repeat(max - used));
    }
    out
}

/// `s` centered inside exactly `max` cells (used for the right-hand pills).
pub fn center(s: &str, max: usize) -> String {
    let text = truncate(s, max);
    let w = width(&text);
    let left = max.saturating_sub(w) / 2;
    let right = max.saturating_sub(w).saturating_sub(left);
    let mut out = String::with_capacity(max);
    out.push_str(&" ".repeat(left));
    out.push_str(&text);
    out.push_str(&" ".repeat(right));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fit_and_center_are_exact_widths() {
        // Plain ASCII behaves like character counts.
        assert_eq!(truncate("abcdef", 6), "abcdef");
        assert_eq!(truncate("abcdef", 5), "ab...");
        assert_eq!(fit("ab", 5), "ab   ");
        assert_eq!(center("[ON]", 10), "   [ON]   ");
        assert_eq!(center("[COLLAPSE]", 10), "[COLLAPSE]");

        // Wide glyphs count as two cells.
        assert_eq!(width("🔤"), 2);
        assert_eq!(fit("🔤", 2), "🔤");
        assert_eq!(fit("🔤", 3), "🔤 ");
        assert_eq!(width(&fit("␣", 2)), 2);

        // Every result occupies exactly the requested width, even when narrow
        // or when the text contains wide/multi-byte glyphs.
        let samples = [
            "🔤",
            "␣",
            "✍️ Spell Command",
            "ÄÖÜ áéí 日本語 ěščř",
            "[COLLAPSE]",
        ];
        for s in samples {
            for max in 0..24 {
                assert_eq!(width(&fit(s, max)), max, "fit({s:?}, {max})");
                assert_eq!(width(&center(s, max)), max, "center({s:?}, {max})");
                assert!(width(&truncate(s, max)) <= max, "truncate({s:?}, {max})");
            }
        }
    }
}

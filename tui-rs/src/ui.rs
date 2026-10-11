//! Rendering of the inline prompt line and the scrollback blocks.
//!
//! Layout mirrors `_render_status_bar()`, `print_transcription()` and `print_event()`
//! in `src/tui.py`.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Paragraph, Wrap};

use unicode_width::UnicodeWidthChar;

use crate::app::{App, Level, OutStatus, RunState};

pub const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn bold(c: Color) -> Style {
    Style::default().fg(c).add_modifier(Modifier::BOLD)
}

fn dim() -> Style {
    Style::default().add_modifier(Modifier::DIM)
}

fn cyan() -> Style {
    Style::default().fg(Color::Cyan)
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() > max {
        let head: String = s.chars().take(max.saturating_sub(3)).collect();
        format!("{head}...")
    } else {
        s.to_string()
    }
}

/// Banner printed once, above the viewport, at startup.
pub fn header_line(app: &App) -> Line<'static> {
    let c = app.effective_color().color();
    Line::from(vec![
        Span::styled("vt ", bold(c)),
        Span::styled("❯ ", bold(c)),
        Span::styled(
            format!("voice transcriber v{} active ", app.version),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!(
                "(model: {} │ mic: {})",
                app.model_backend.to_uppercase(),
                app.active_device
            ),
            dim(),
        ),
    ])
}

/// The inline prompt / status bar as Text.
pub fn status_text(app: &App, width: u16) -> Text<'static> {
    status_text_for(app, &app.state, &app.sub_state_text, width)
}

pub fn status_text_for(app: &App, state: &RunState, sub_state: &str, width: u16) -> Text<'static> {
    Text::from(status_lines_for(app, state, sub_state, width))
}

/// What the meeting screen reserves for the live transcript, in lines. The
/// count is fixed so `max_status_height` - which samples states once to size the
/// inline viewport - never grows as text accumulates. See `tail_wrapped`.
const MEETING_TEXT_LINES: usize = 3;

/// Hard-wrap the **tail** of `text` into exactly `max_lines` lines of at most
/// `width` display cells, padding the top with empty lines.
///
/// The meeting transcript only ever grows, so the renderer must not let the
/// row's height follow it: the inline viewport is sized once from the tallest
/// state, and resizing it wipes scrollback. Pre-wrapping to a fixed box (whose
/// lines are already <= `width`, so `Paragraph`'s own wrapping is a no-op) makes
/// the row a constant height whatever the text and the terminal width. Only the
/// tail is kept because the newest words are the interesting ones.
fn tail_wrapped(text: &str, width: usize, max_lines: usize) -> Vec<String> {
    if width == 0 || max_lines == 0 {
        return vec![String::new(); max_lines];
    }
    // Walk backwards only far enough to fill the box: the transcript can be
    // hours long and this keeps the per-frame work bounded by the visible
    // window rather than the whole text.
    let budget = width * max_lines;
    let mut chars: Vec<char> = Vec::new();
    let mut used = 0usize;
    for c in text.chars().rev() {
        let cw = UnicodeWidthChar::width(c).unwrap_or(0);
        if used + cw > budget {
            break;
        }
        chars.push(c);
        used += cw;
    }
    chars.reverse();

    // Hard-wrap, as any terminal would: a word longer than the box is split.
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut cur_w = 0usize;
    for c in chars {
        let cw = UnicodeWidthChar::width(c).unwrap_or(0);
        if cur_w + cw > width && !cur.is_empty() {
            lines.push(std::mem::take(&mut cur));
            cur_w = 0;
        }
        cur.push(c);
        cur_w += cw;
    }
    if !cur.is_empty() {
        lines.push(cur);
    }
    // A line that is only whitespace counts as two rendered lines at some
    // widths (ratatui wraps its trailing space into a phantom line), which
    // would make the height depend on where a word happens to fall. The spaces
    // are invisible anyway, so trim them off; an all-space split then collapses
    // to an empty line and counts like every other blank.
    for line in lines.iter_mut() {
        *line = line.trim().to_string();
    }
    // Keep the newest lines and pad the top, so the newest text always sits on
    // the bottom row and the reader's eye stays put.
    while lines.len() > max_lines {
        lines.remove(0);
    }
    while lines.len() < max_lines {
        lines.insert(0, String::new());
    }
    lines
}

/// Render the prompt lines for an arbitrary (state, sub-text) pair. Used so we can
/// measure the tallest state without mutating the app.
pub fn status_lines_for(
    app: &App,
    state: &RunState,
    sub_state: &str,
    width: u16,
) -> Vec<Line<'static>> {
    let c = app.effective_color().color();

    match state {
        RunState::Ready => {
            let mut l1: Vec<Span<'static>> = vec![
                Span::styled("❯ ", bold(c)),
                Span::styled("ready ", bold(c)),
                Span::styled("│ ", dim()),
                Span::styled(format!("mic: {} ", truncate(&app.active_device, 18)), cyan()),
                Span::styled("│ ", dim()),
                Span::styled(format!("model: {} ", app.model_backend.to_lowercase()), cyan()),
                Span::styled("│ ", dim()),
            ];
            if app.is_muted {
                l1.push(Span::styled("sound: off ", Style::default().fg(Color::Red).add_modifier(Modifier::DIM)));
            } else {
                l1.push(Span::styled("sound: on ", Style::default().fg(Color::Green)));
            }
            l1.push(Span::styled("│ ", dim()));
            match app.output_mode.as_str() {
                "type" => l1.push(Span::styled("auto-type (slow) ", Style::default().fg(Color::Green).add_modifier(Modifier::BOLD))),
                "type_fast" => l1.push(Span::styled("auto-type (fast) ", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD))),
                _ => l1.push(Span::styled("clipboard ", cyan())),
            }

            if app.auto_type {
                l1.push(Span::styled("│ ", dim()));
                if app.trailing_space {
                    l1.push(Span::styled("space: on ", Style::default().fg(Color::Green)));
                } else {
                    l1.push(Span::styled("space: off ", dim()));
                }

                l1.push(Span::styled("│ ", dim()));
                if app.auto_punctuate {
                    l1.push(Span::styled("auto-punct: on ", Style::default().fg(Color::Green)));
                } else {
                    l1.push(Span::styled("auto-punct: off ", dim()));
                }
            }

            l1.push(Span::styled("│ ", dim()));
            let punc_disp = match app.punctuation_mode.as_str() {
                "no_terminal_period" | "casual" => "casual",
                "no_punctuation" | "autocorrect" => "autocorrect",
                "aesthetic_lowercase" | "aesthetic" => "aesthetic",
                "lowercase_no_punctuation" | "gen_z" => "gen-z",
                _ => "default",
            };
            l1.push(Span::styled(format!("preset: {punc_disp} "), cyan()));

            l1.push(Span::styled("│ ", dim()));
            match app.number_mode.as_str() {
                "digits" => l1.push(Span::styled("num: digits ", Style::default().fg(Color::Green))),
                "words" => l1.push(Span::styled("num: words ", dim())),
                _ => l1.push(Span::styled("num: auto ", cyan())),
            }

            l1.push(Span::styled("│ ", dim()));
            if app.middle_click_enabled {
                l1.push(Span::styled("mouse: on ", Style::default().fg(Color::Green)));
            } else {
                l1.push(Span::styled("mouse: off ", dim()));
            }

            let l2: Vec<Span<'static>> = vec![
                Span::styled("  [Space] Rec (tap again = stop, hands-free)  [s/S/,] Settings  [r] Reset  [q] Quit", dim()),
            ];

            vec![Line::from(l1), Line::from(l2)]
        }
        RunState::Recording => {
            let mut l1: Vec<Span<'static>> = vec![
                Span::styled("❯ ", bold(c)),
                Span::styled(
                    "RECORDING ",
                    Style::default().fg(Color::White).bg(Color::Red).add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
                Span::styled(
                    format!("[{:04.1}s] ", app.elapsed_secs()),
                    Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
                ),
                Span::styled("│ ", dim()),
            ];
            l1.extend(vu_spans(app.vu_level));
            l1.push(Span::styled(
                format!(" ({}%) ", (app.vu_level * 100.0) as i32),
                Style::default().fg(Color::Cyan).add_modifier(Modifier::DIM),
            ));
            l1.push(Span::styled("│ ", dim()));
            l1.push(Span::styled(format!("mic: {} ", truncate(&app.active_device, 18)), cyan()));

            let l2: Vec<Span<'static>> = vec![
                Span::styled("  [Space] Stop · release Alt+Shift / Middle-Click if you used push-to-talk", dim()),
            ];

            vec![Line::from(l1), Line::from(l2)]
        }
        RunState::Meeting => {
            // Long, non-injecting capture. Python drives the elapsed timer and
            // batch-phase percentage in the sub-text, so this row just shows it.
            let msg = if sub_state.is_empty() {
                "long capture — nothing is typed".to_string()
            } else {
                sub_state.to_string()
            };
            let mut lines = vec![Line::from(vec![
                Span::styled("❯ ", bold(c)),
                Span::styled(
                    "MEETING ",
                    Style::default()
                        .fg(Color::White)
                        .bg(Color::Blue)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
                Span::styled(msg, Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD)),
            ])];
            // The live transcript the engine streams while a capture records.
            // It is pre-wrapped into a fixed box (see `tail_wrapped`) so the
            // row's height cannot grow as words accumulate; the empty state
            // keeps the same height and says what is happening instead.
            let wrap_w = width.max(1) as usize;
            // Speaker labels may carry newlines; flatten them so a line break
            // cannot add height the fixed box did not budget for.
            let live = app.meeting_live_text.replace(['\n', '\r', '\t'], " ");
            let text = live.trim();
            if text.is_empty() {
                let hint = crate::textfit::truncate(
                    "listening · Space stops · labels when you stop",
                    wrap_w,
                );
                lines.push(Line::from(Span::styled(hint, dim())));
                for _ in 1..MEETING_TEXT_LINES {
                    lines.push(Line::from(""));
                }
            } else {
                for row in tail_wrapped(text, wrap_w, MEETING_TEXT_LINES) {
                    lines.push(Line::from(Span::styled(
                        row,
                        Style::default().fg(Color::White),
                    )));
                }
            }
            lines
        }
        RunState::Processing => {
            let spinner = SPINNER[app.spinner % SPINNER.len()];
            let is_loading = {
                let lower = sub_state.to_ascii_lowercase();
                lower.contains("load") || lower.contains("download")
            };
            let label = if is_loading { "LOADING MODEL" } else { "PROCESSING AUDIO" };
            let msg = if sub_state.is_empty() {
                format!(
                    "Transcribing stream with {}...",
                    titlecase(&app.model_backend)
                )
            } else {
                sub_state.to_string()
            };
            vec![Line::from(vec![
                Span::styled("❯ ", bold(c)),
                Span::styled(
                    format!("{spinner} {label} [{:04.1}s] ", app.elapsed_secs()),
                    Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
                ),
                Span::styled("│ ", dim()),
                Span::styled(msg, Style::default().fg(Color::White)),
            ])]
        }
        RunState::Rewriting => {
            let spinner = SPINNER[app.spinner % SPINNER.len()];
            vec![Line::from(vec![
                Span::styled("❯ ", bold(c)),
                Span::styled(
                    format!("🤖 {spinner} REFINING GRAMMAR [{:04.1}s] ", app.elapsed_secs()),
                    Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD),
                ),
                Span::styled("│ ", dim()),
                Span::styled("SLM polishing text...", Style::default().fg(Color::White)),
            ])]
        }
        RunState::Config(cfg) => {
            let mut spans: Vec<Span<'static>> = vec![
                Span::styled("❯ ", bold(c)),
                Span::styled("CONFIG ", bold(Color::White)),
            ];
            if !cfg.is_empty() {
                spans.push(Span::styled(format!("│ {cfg}"), dim()));
            }
            vec![Line::from(spans)]
        }
    }
}

/// Height needed by the status bar for `state` at `width`.
pub fn status_height_for(app: &App, state: &RunState, sub: &str, width: u16) -> u16 {
    Paragraph::new(status_text_for(app, state, sub, width))
        .wrap(Wrap { trim: false })
        .line_count(width)
        .max(1) as u16
}

/// The tallest status bar across all states at `width`.
///
/// The inline viewport is sized to this once and then left alone: calling
/// `Terminal::resize` mid-session clears the region below the viewport and would
/// wipe recently inserted scrollback, so we bottom-align shorter states instead.
pub fn max_status_height(app: &App, width: u16) -> u16 {
    let mut full_app = App::new(&app.version, app.ui_theme);
    full_app.active_device = if app.active_device.is_empty() || app.active_device == "Detecting..." {
        "Primary: default microphone".to_string()
    } else {
        app.active_device.clone()
    };
    full_app.auto_type = true;
    full_app.output_mode = "type_fast".to_string();
    full_app.trailing_space = true;
    full_app.auto_punctuate = true;
    full_app.number_digits = true;
    full_app.number_mode = "auto".to_string();
    full_app.middle_click_enabled = true;
    full_app.punctuation_mode = "lowercase_no_punctuation".to_string();

    let samples = [
        RunState::Ready,
        RunState::Recording,
        RunState::Meeting,
        RunState::Processing,
        RunState::Rewriting,
        RunState::Config("Selecting audio device (1/4)".to_string()),
    ];
    samples
        .iter()
        .map(|s| status_height_for(&full_app, s, "Warming up model weights and audio device.", width))
        .max()
        .unwrap_or(1)
}

fn vu_spans(level: f32) -> Vec<Span<'static>> {
    let bar_len = 16usize;
    let mut filled_len = (level.min(1.0) * 3.5 * bar_len as f32) as usize;
    filled_len = filled_len.min(bar_len);
    let empty_len = bar_len - filled_len;
    let chars: Vec<char> = "█"
        .repeat(filled_len)
        .chars()
        .chain("░".repeat(empty_len).chars())
        .collect();

    let green = Style::default().fg(Color::Green);
    let yellow = Style::default().fg(Color::Yellow);
    let red = Style::default().fg(Color::Red);
    let seg = |a: usize, b: usize, style: Style| {
        Span::styled(chars[a..b].iter().collect::<String>(), style)
    };

    if filled_len > 12 {
        vec![seg(0, 9, green), seg(9, 13, yellow), seg(13, bar_len, red)]
    } else if filled_len > 7 {
        vec![seg(0, 7, green), seg(7, bar_len, yellow)]
    } else {
        vec![seg(0, bar_len, green)]
    }
}

/// A block that gets pushed into terminal scrollback above the inline viewport.
pub struct Block {
    pub top: Line<'static>,
    pub body: String,
    pub bottom: Line<'static>,
}

fn format_duration(seconds: f32) -> String {
    let total_sec = seconds.round().max(0.0) as u64;
    if total_sec < 60 {
        format!("{total_sec}s")
    } else if total_sec < 3600 {
        let mins = total_sec / 60;
        let secs = total_sec % 60;
        format!("{mins}m {secs:02}s")
    } else {
        let hours = total_sec / 3600;
        let mins = (total_sec % 3600) / 60;
        format!("{hours}h {mins:02}m")
    }
}

fn format_optional_duration(value: Option<f32>) -> Option<String> {
    // Mirrors tui.py::_format_optional_duration: nothing worth showing -> None.
    value.filter(|v| *v > 0.0).map(format_duration)
}

#[allow(clippy::too_many_arguments)] // one arg per wire/stat field; a struct would only move the noise
pub fn transcription_block(
    app: &App,
    width: u16,
    text: &str,
    rec: f32,
    proc: f32,
    ready: f32,
    status: OutStatus,
    time_saved: Option<f32>,
    session_time_saved: Option<f32>,
    lifetime_time_saved: Option<f32>,
) -> Block {
    let c = app.effective_color().color();
    let ts = chrono::Local::now().format("%H:%M:%S").to_string();
    let (status_str, status_color) = match status {
        OutStatus::Typed => ("Copied & Typed", Color::Green),
        OutStatus::Copied => ("Copied to Clipboard", Color::Cyan),
        OutStatus::CopyError => ("Clipboard Error", Color::Red),
    };

    let mut spans: Vec<Span<'static>> = vec![
        Span::styled("────", bold(c)),
        Span::styled(format!(" ❯ #{} ", app.transcription_count), bold(c)),
        Span::styled(format!(" {ts} "), dim()),
    ];

    if rec > 0.0 {
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(format!("rec: {rec:.2}s "), cyan()));
    }
    if proc > 0.0 {
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(
            format!("proc: {proc:.2}s "),
            Style::default().fg(Color::Yellow),
        ));
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(
            format!("ready: {ready:.2}s "),
            Style::default().fg(Color::LightCyan),
        ));
    } else {
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(
            format!("proc: {proc:.2}s "),
            Style::default().fg(Color::Yellow),
        ));
    }
    if let Some(saved) = time_saved {
        if saved > 0.0 {
            spans.push(Span::styled("│ ", dim()));
            let session = format_optional_duration(session_time_saved);
            let lifetime = format_optional_duration(lifetime_time_saved);
            let totals = match (&session, &lifetime) {
                (Some(s), Some(l)) if s != l => {
                    Some(format!("(session: {s} · total: {l})"))
                }
                // Fresh session: the all-time total is the interesting number.
                (_, Some(l)) => Some(format!("(total: {l})")),
                (Some(s), None) => Some(format!("(session: {s})")),
                (None, None) => None,
            };
            let badge = match totals {
                Some(totals) => {
                    format!("⚡ saved: +{} {totals} ", format_duration(saved))
                }
                None => format!("⚡ saved: +{} ", format_duration(saved)),
            };
            spans.push(Span::styled(badge, bold(Color::Green)));
        }
    }
    spans.push(Span::styled("│ ", dim()));
    spans.push(Span::styled(
        status_str,
        Style::default().fg(status_color),
    ));

    Block {
        top: Line::from(spans),
        body: format!("  {}", text.trim()),
        bottom: Line::from(Span::styled(
            "─".repeat(width as usize),
            Style::default().fg(c),
        )),
    }
}

pub fn event_block(app: &App, width: u16, title: &str, message: &str, level: Level) -> Block {
    let c = app.effective_color().color();
    let color = level.color(c);
    let ts = chrono::Local::now().format("%H:%M:%S").to_string();
    let title_display = match level {
        Level::Warning => format!("⚠️ {title}"),
        Level::Error => format!("❌ {title}"),
        _ => title.to_string(),
    };

    let used = 4 + format!(" ⚙️ {title_display} ").chars().count() + ts.len() + 4;
    let right = (width as usize).saturating_sub(used).max(2);

    let top = Line::from(vec![
        Span::styled("────", bold(color)),
        Span::styled(
            format!(" ⚙️ {title_display} "),
            bold(color),
        ),
        Span::styled(format!(" [{ts}] "), dim()),
        Span::styled("─".repeat(right), bold(color)),
    ]);

    Block {
        top,
        body: format!("  {message}"),
        bottom: Line::from(Span::styled(
            "─".repeat(width as usize),
            Style::default().fg(color),
        )),
    }
}

/// Number of terminal lines a block occupies once its body wraps.
pub fn block_height(block: &Block, width: u16) -> u16 {
    let body = Paragraph::new(block.body.clone())
        .wrap(Wrap { trim: false })
        .line_count(width)
        .max(1) as u16;
    body + 2
}

fn titlecase(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(first) => first.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{App, Level, Theme};
    use ratatui::style::Color;

    #[test]
    fn test_event_block_colors_follow_theme() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        assert_eq!(app.effective_color(), Theme::Cyan);

        // Model Ready (Success) should follow theme (Cyan)
        let block_success = event_block(&app, 80, "✅ Model Ready", "Model loaded", Level::Success);
        let top_spans = &block_success.top.spans;
        assert_eq!(top_spans[0].style.fg, Some(Color::Cyan));
        assert_eq!(top_spans[1].style.fg, Some(Color::Cyan));
        assert_eq!(block_success.bottom.spans[0].style.fg, Some(Color::Cyan));

        // Trailing Space (Info) should follow theme (Cyan)
        let block_info = event_block(&app, 80, "␣ Trailing Space", "Enabled", Level::Info);
        let top_spans_info = &block_info.top.spans;
        assert_eq!(top_spans_info[0].style.fg, Some(Color::Cyan));
        assert_eq!(block_info.bottom.spans[0].style.fg, Some(Color::Cyan));

        // No Speech Detected (Warning) should follow theme (Cyan)
        let block_warn = event_block(&app, 80, "No Speech Detected", "No audio", Level::Warning);
        let top_spans_warn = &block_warn.top.spans;
        assert_eq!(top_spans_warn[0].style.fg, Some(Color::Cyan));
        assert_eq!(block_warn.bottom.spans[0].style.fg, Some(Color::Cyan));

        // Critical Error should stay Red
        let block_err = event_block(&app, 80, "Error", "Failure", Level::Error);
        let top_spans_err = &block_err.top.spans;
        assert_eq!(top_spans_err[0].style.fg, Some(Color::Red));
        assert_eq!(block_err.bottom.spans[0].style.fg, Some(Color::Red));

        // Switch to Magenta theme and verify
        app.ui_theme = Theme::Magenta;
        let block_magenta = event_block(&app, 80, "✅ Model Ready", "Model loaded", Level::Success);
        assert_eq!(block_magenta.top.spans[0].style.fg, Some(Color::Magenta));
        assert_eq!(block_magenta.bottom.spans[0].style.fg, Some(Color::Magenta));
    }

    #[test]
    fn test_transcription_block_time_saved_badge() {
        let app = App::new("1.1.1", Theme::Cyan);
        let block = transcription_block(
            &app,
            80,
            "hello world",
            1.5,
            0.5,
            0.2,
            OutStatus::Typed,
            Some(12.0),
            Some(105.0),
            None,
        );
        let top_str: String = block.top.spans.iter().map(|s| s.content.as_ref()).collect();
        assert!(top_str.contains("⚡ saved: +12s (session: 1m 45s)"));
    }

    #[test]
    fn test_transcription_block_lifetime_badge() {
        let app = App::new("1.1.1", Theme::Cyan);
        let block = transcription_block(
            &app,
            80,
            "hello world",
            1.5,
            0.5,
            0.2,
            OutStatus::Typed,
            Some(12.0),
            Some(105.0),
            Some(3600.0),
        );
        let top_str: String = block.top.spans.iter().map(|s| s.content.as_ref()).collect();
        assert!(top_str.contains("⚡ saved: +12s (session: 1m 45s · total: 1h 00m)"));
    }

    /// Parity with tui.format_duration_helper / t2.format_duration in Python.
    ///
    /// Rust's f32::round rounds half away from zero; Python's builtin round() is
    /// banker's rounding (round(2.5) == 2). The Python side was aligned to
    /// half-up so both frontends print the same badge. Keep these expected values
    /// in sync with tests/shared/test_time_saved.py::
    /// test_half_seconds_round_up_like_the_rust_frontend.
    #[test]
    fn test_format_duration_matches_the_python_frontend() {
        assert_eq!(format_duration(0.0), "0s");
        assert_eq!(format_duration(14.0), "14s");
        assert_eq!(format_duration(14.4), "14s");
        assert_eq!(format_duration(14.5), "15s");
        assert_eq!(format_duration(0.5), "1s");
        assert_eq!(format_duration(2.5), "3s");
        assert_eq!(format_duration(12.5), "13s");
        assert_eq!(format_duration(62.5), "1m 03s");
        assert_eq!(format_duration(3599.5), "1h 00m");
        assert_eq!(format_duration(135.0), "2m 15s");
        assert_eq!(format_duration(3900.0), "1h 05m");
        assert_eq!(format_duration(-5.0), "0s");
    }

    /// The property that matters for the live transcript: whatever it says, the
    /// meeting row must occupy the same number of lines, because
    /// `max_status_height` sizes the inline viewport once and a growing row
    /// would move the whole layout (and resizing the viewport wipes scrollback).
    #[test]
    fn test_meeting_live_text_keeps_a_constant_status_height() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.update_state(RunState::Meeting, "01:05 · 40%".to_string());

        let samples = [
            String::new(),
            "Alice: hello".to_string(),
            "word ".repeat(500),
            "a".repeat(4_000),
            "Alice: hello\nBob: hi\n".repeat(50),
        ];
        for width in [1u16, 2, 3, 20, 40, 80, 120] {
            app.meeting_live_text.clear();
            let baseline =
                status_height_for(&app, &RunState::Meeting, &app.sub_state_text, width);
            for text in &samples {
                app.meeting_live_text = text.clone();
                let h = status_height_for(&app, &RunState::Meeting, &app.sub_state_text, width);
                assert_eq!(h, baseline, "height changed at width {width} for {text:?}");
            }
        }
    }

    #[test]
    fn test_meeting_status_renders_on_a_tiny_terminal() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.update_state(RunState::Meeting, "01:05 · 40%".to_string());
        app.meeting_live_text =
            "Alice: a long transcript that keeps accumulating ".repeat(50);
        for (w, h) in [(80, 24), (174, 3), (20, 2), (1, 1)] {
            let backend = ratatui::backend::TestBackend::new(w, h);
            let mut terminal = ratatui::Terminal::new(backend).unwrap();
            terminal
                .draw(|f| {
                    let text = status_text(&app, f.area().width);
                    f.render_widget(
                        Paragraph::new(text).wrap(Wrap { trim: false }),
                        f.area(),
                    );
                })
                .unwrap_or_else(|e| panic!("{w}x{h} failed to draw: {e}"));
        }
    }
}

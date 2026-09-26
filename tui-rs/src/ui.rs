//! Rendering of the inline prompt line and the scrollback blocks.
//!
//! Layout mirrors `_render_status_bar()`, `print_transcription()` and `print_event()`
//! in `src/tui.py`.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Paragraph, Wrap};

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
pub fn status_text(app: &App) -> Text<'static> {
    status_text_for(app, &app.state, &app.sub_state_text)
}

/// The inline prompt / status bar lines.
#[allow(dead_code)]
pub fn status_lines(app: &App) -> Vec<Line<'static>> {
    status_lines_for(app, &app.state, &app.sub_state_text)
}

/// The inline prompt / status bar (first line). Mirrors `_render_status_bar()`.
#[allow(dead_code)]
pub fn status_line(app: &App) -> Line<'static> {
    status_line_for(app, &app.state, &app.sub_state_text)
}

pub fn status_text_for(app: &App, state: &RunState, sub_state: &str) -> Text<'static> {
    Text::from(status_lines_for(app, state, sub_state))
}

#[allow(dead_code)]
pub fn status_line_for(app: &App, state: &RunState, sub_state: &str) -> Line<'static> {
    status_lines_for(app, state, sub_state)
        .into_iter()
        .next()
        .unwrap_or_else(|| Line::from(vec![]))
}

/// Render the prompt lines for an arbitrary (state, sub-text) pair. Used so we can
/// measure the tallest state without mutating the app.
pub fn status_lines_for(app: &App, state: &RunState, sub_state: &str) -> Vec<Line<'static>> {
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
                "no_terminal_period" => "no-period",
                "no_punctuation" => "none",
                "lowercase_no_punctuation" => "lower",
                _ => "full",
            };
            l1.push(Span::styled(format!("punc: {punc_disp} "), cyan()));

            l1.push(Span::styled("│ ", dim()));
            if app.number_digits {
                l1.push(Span::styled("num: digits ", Style::default().fg(Color::Green)));
            } else {
                l1.push(Span::styled("num: words ", dim()));
            }

            l1.push(Span::styled("│ ", dim()));
            if app.middle_click_enabled {
                l1.push(Span::styled("mouse: on ", Style::default().fg(Color::Green)));
            } else {
                l1.push(Span::styled("mouse: off ", dim()));
            }

            let l2: Vec<Span<'static>> = vec![
                Span::styled("  [Space] Rec  [S/,] Settings  [t] Theme  [M] Mic  [m] Mute  [c] Mode  [s] Space  [p] Punc  [n] Num  [q] Quit", dim()),
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
                Span::styled("  Release Alt+Shift or Middle Click to finish · Space while holding = hands-free", dim()),
            ];

            vec![Line::from(l1), Line::from(l2)]
        }
        RunState::Processing => {
            let spinner = SPINNER[app.spinner % SPINNER.len()];
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
                    format!("{spinner} PROCESSING AUDIO [{:04.1}s] ", app.elapsed_secs()),
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
    Paragraph::new(status_text_for(app, state, sub))
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
    full_app.middle_click_enabled = true;
    full_app.punctuation_mode = "lowercase_no_punctuation".to_string();

    let samples = [
        RunState::Ready,
        RunState::Recording,
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

pub fn transcription_block(
    app: &App,
    width: u16,
    text: &str,
    rec: f32,
    proc: f32,
    ready: f32,
    status: OutStatus,
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
}

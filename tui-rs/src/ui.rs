//! Rendering of the inline prompt line and the scrollback blocks.
//!
//! Layout mirrors `_render_status_bar()`, `print_transcription()` and `print_event()`
//! in `src/tui.py`.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
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

/// The inline prompt / status bar. Mirrors `_render_status_bar()`.
pub fn status_line(app: &App) -> Line<'static> {
    status_line_for(app, &app.state, &app.sub_state_text)
}

/// Render the prompt line for an arbitrary (state, sub-text) pair. Used so we can
/// measure the tallest state without mutating the app.
pub fn status_line_for(app: &App, state: &RunState, sub_state: &str) -> Line<'static> {
    let c = app.effective_color().color();
    let mut spans: Vec<Span<'static>> = vec![Span::styled("❯ ", bold(c))];

    match state {
        RunState::Ready => {
            spans.push(Span::styled("ready ", bold(c)));
            spans.push(Span::styled("│ ", dim()));
            spans.push(Span::styled(
                format!("mic: {} ", truncate(&app.active_device, 18)),
                cyan(),
            ));
            spans.push(Span::styled("│ ", dim()));
            spans.push(Span::styled(
                format!("model: {} ", app.model_backend.to_lowercase()),
                cyan(),
            ));
            spans.push(Span::styled("│ ", dim()));
            if app.is_muted {
                spans.push(Span::styled(
                    "sound: off ",
                    Style::default().fg(Color::Red).add_modifier(Modifier::DIM),
                ));
            } else {
                spans.push(Span::styled(
                    "sound: on ",
                    Style::default().fg(Color::Green),
                ));
            }
            spans.push(Span::styled("│ ", dim()));
            if app.auto_type {
                spans.push(Span::styled(
                    "auto-type ",
                    Style::default().fg(Color::Magenta),
                ));
            } else {
                spans.push(Span::styled("clipboard ", cyan()));
            }
            spans.push(Span::styled("│ ", dim()));
            spans.push(Span::styled(
                "[Space] Rec  [M] Mic  [m] Mute  [n] Numbers  [o] Mouse  [c] Clipboard  [t] Theme  [q] Quit",
                dim(),
            ));
        }
        RunState::Recording => {
            spans.push(Span::styled(
                "RECORDING ",
                Style::default()
                    .fg(Color::White)
                    .bg(Color::Red)
                    .add_modifier(Modifier::BOLD),
            ));
            spans.push(Span::raw(" "));
            spans.push(Span::styled(
                format!("[{:04.1}s] ", app.elapsed_secs()),
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ));
            spans.push(Span::styled("│ ", dim()));
            spans.extend(vu_spans(app.vu_level));
            spans.push(Span::styled(
                format!(" ({}%) ", (app.vu_level * 100.0) as i32),
                Style::default().fg(Color::Cyan).add_modifier(Modifier::DIM),
            ));
            spans.push(Span::styled("│ ", dim()));
            spans.push(Span::styled(
                "Release Alt+Shift or Middle Click to finish · Space while holding = hands-free",
                dim(),
            ));
        }
        RunState::Processing => {
            let spinner = SPINNER[app.spinner % SPINNER.len()];
            spans.push(Span::styled(
                format!("{spinner} PROCESSING AUDIO [{:04.1}s] ", app.elapsed_secs()),
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ));
            spans.push(Span::styled("│ ", dim()));
            if sub_state.is_empty() {
                spans.push(Span::styled(
                    format!(
                        "Transcribing stream with {}...",
                        titlecase(&app.model_backend)
                    ),
                    Style::default().fg(Color::White),
                ));
            } else {
                spans.push(Span::styled(
                    sub_state.to_string(),
                    Style::default().fg(Color::White),
                ));
            }
        }
        RunState::Rewriting => {
            let spinner = SPINNER[app.spinner % SPINNER.len()];
            spans.push(Span::styled(
                format!(
                    "🤖 {spinner} REFINING GRAMMAR [{:04.1}s] ",
                    app.elapsed_secs()
                ),
                Style::default()
                    .fg(Color::Magenta)
                    .add_modifier(Modifier::BOLD),
            ));
            spans.push(Span::styled("│ ", dim()));
            spans.push(Span::styled(
                "SLM polishing text...",
                Style::default().fg(Color::White),
            ));
        }
        RunState::Config(cfg) => {
            spans.push(Span::styled("CONFIG ", bold(Color::White)));
            if !cfg.is_empty() {
                spans.push(Span::styled(format!("│ {cfg}"), dim()));
            }
        }
    }

    Line::from(spans)
}

/// Height needed by the status bar for `state` at `width`.
pub fn status_height_for(app: &App, state: &RunState, sub: &str, width: u16) -> u16 {
    Paragraph::new(status_line_for(app, state, sub))
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
    let samples = [
        RunState::Ready,
        RunState::Recording,
        RunState::Processing,
        RunState::Rewriting,
        RunState::Config("Selecting audio device (1/4)".to_string()),
    ];
    samples
        .iter()
        .map(|s| status_height_for(app, s, "Warming up model weights and audio device.", width))
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

    let mut used = 4 + format!(" ❯ #{} ", app.transcription_count).chars().count() + ts.len() + 2;

    if rec > 0.0 {
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(format!("rec: {rec:.2}s "), cyan()));
        used += 2 + format!("rec: {rec:.2}s ").chars().count();
    }
    if proc > 0.0 {
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(
            format!("proc: {proc:.2}s "),
            Style::default().fg(Color::Yellow),
        ));
        used += 2 + format!("proc: {proc:.2}s ").chars().count();
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(
            format!("ready: {ready:.2}s "),
            Style::default().fg(Color::LightCyan),
        ));
        used += 2 + format!("ready: {ready:.2}s ").chars().count();
    } else {
        spans.push(Span::styled("│ ", dim()));
        spans.push(Span::styled(
            format!("proc: {proc:.2}s "),
            Style::default().fg(Color::Yellow),
        ));
        used += 2 + format!("proc: {proc:.2}s ").chars().count();
    }
    spans.push(Span::styled("│ ", dim()));
    spans.push(Span::styled(
        format!("{status_str} "),
        Style::default().fg(status_color),
    ));
    used += 2 + status_str.len() + 1;

    let right = (width as usize).saturating_sub(used).max(2);
    spans.push(Span::styled("─".repeat(right), bold(c)));

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
    let _ = app;
    let color = level.color();
    let ts = chrono::Local::now().format("%H:%M:%S").to_string();
    let title_display = match level {
        Level::Warning => format!("⚠️ {title}"),
        Level::Error => format!("❌ {title}"),
        _ => title.to_string(),
    };

    let used = 4 + format!(" ⚙️ {title_display} ").chars().count() + ts.len() + 3;
    let right = (width as usize).saturating_sub(used).max(0);

    let top = Line::from(vec![
        Span::styled("────", Style::default().fg(color)),
        Span::styled(
            format!(" ⚙️ {title_display} "),
            Style::default().fg(color).add_modifier(Modifier::BOLD),
        ),
        Span::styled(format!(" [{ts}] "), dim()),
        Span::styled("─".repeat(right), Style::default().fg(color)),
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

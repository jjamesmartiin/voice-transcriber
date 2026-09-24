//! Interactive settings picker modal with live in-place toggling and fuzzy search.

use std::io;
use std::os::unix::net::UnixStream;
use std::sync::mpsc;
use std::time::Duration;

use crossterm::event::{Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear, Paragraph};
use ratatui::Frame;

use crate::app::App;
use crate::ipc::{self, IpcEvent, Wire};
use crate::theme_picker;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SettingKind {
    TrailingSpace,
    AutoPunctuate,
    NumberDigits,
    MiddleClick,
    SoundMute,
    OutputMode,
    PunctuationMode,
    Theme,
    Microphone,
    ResetTerminal,
}

#[derive(Clone, Copy, Debug)]
pub struct SettingItem {
    pub kind: SettingKind,
    pub icon: &'static str,
    pub title: &'static str,
    pub keywords: &'static str,
}

pub const SETTINGS: [SettingItem; 10] = [
    SettingItem {
        kind: SettingKind::TrailingSpace,
        icon: "␣ ",
        title: "Trailing Space",
        keywords: "trailing space auto type whitespace append space",
    },
    SettingItem {
        kind: SettingKind::AutoPunctuate,
        icon: "📝",
        title: "Auto-Punctuation",
        keywords: "auto punctuate punctuation period sentence grammar enforcement",
    },
    SettingItem {
        kind: SettingKind::NumberDigits,
        icon: "🔢",
        title: "Number Conversion",
        keywords: "numbers digits words spelled format numeric conversion",
    },
    SettingItem {
        kind: SettingKind::MiddleClick,
        icon: "🔘",
        title: "Mouse Hotkey",
        keywords: "middle click mouse hotkey push to talk button hold",
    },
    SettingItem {
        kind: SettingKind::SoundMute,
        icon: "🔊",
        title: "Sound Effects",
        keywords: "sound effects mute volume chimes audio audio cues notify",
    },
    SettingItem {
        kind: SettingKind::OutputMode,
        icon: "📋",
        title: "Output Mode",
        keywords: "output mode clipboard type typing paste speed fast slow safe",
    },
    SettingItem {
        kind: SettingKind::PunctuationMode,
        icon: "📄",
        title: "Formatting Mode",
        keywords: "formatting mode punctuation period lowercase none full",
    },
    SettingItem {
        kind: SettingKind::Theme,
        icon: "🎨",
        title: "UI Color Theme",
        keywords: "theme color ui palette cyan magenta green blue yellow red white",
    },
    SettingItem {
        kind: SettingKind::Microphone,
        icon: "🎤",
        title: "Audio Device (Mic)",
        keywords: "mic microphone audio device input hardware primary secondary",
    },
    SettingItem {
        kind: SettingKind::ResetTerminal,
        icon: "🔄",
        title: "Reset Terminal",
        keywords: "reset terminal clipboard bridge clear state fix",
    },
];

impl SettingItem {
    pub fn value_and_badge(&self, app: &App) -> (String, &'static str, Color) {
        let c = app.effective_color().color();
        match self.kind {
            SettingKind::TrailingSpace => {
                if app.trailing_space {
                    ("Enabled (appends ' ')".to_string(), "[ON]", Color::Green)
                } else {
                    ("Disabled (exact text)".to_string(), "[OFF]", Color::DarkGray)
                }
            }
            SettingKind::AutoPunctuate => {
                if app.auto_punctuate {
                    ("Enabled (full + period)".to_string(), "[ON]", Color::Green)
                } else {
                    ("Disabled (preserve user)".to_string(), "[OFF]", Color::DarkGray)
                }
            }
            SettingKind::NumberDigits => {
                if app.number_digits {
                    ("Digits (1, 2, 3)".to_string(), "[DIGITS]", Color::Green)
                } else {
                    (
                        "Words (one, two, three)".to_string(),
                        "[WORDS]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::MiddleClick => {
                if app.middle_click_enabled {
                    ("Enabled (hold middle click)".to_string(), "[ON]", Color::Green)
                } else {
                    ("Disabled".to_string(), "[OFF]", Color::DarkGray)
                }
            }
            SettingKind::SoundMute => {
                if !app.is_muted {
                    ("Enabled (sound chimes on)".to_string(), "[ON]", Color::Green)
                } else {
                    ("Muted (silent)".to_string(), "[MUTED]", Color::Red)
                }
            }
            SettingKind::OutputMode => {
                let (desc, badge) = match app.output_mode.as_str() {
                    "type_fast" => ("Auto-Type (Fast / Optimized)", "[FAST]"),
                    "type" => ("Auto-Type (Slow / Safe)", "[SLOW]"),
                    "paste" => ("Clipboard + Paste (Ctrl+V)", "[PASTE]"),
                    "paste_terminal" => ("Clipboard + Term Paste", "[TERM]"),
                    _ => ("Clipboard Only", "[CLIP]"),
                };
                (desc.to_string(), badge, Color::Cyan)
            }
            SettingKind::PunctuationMode => {
                let (desc, badge) = match app.punctuation_mode.as_str() {
                    "no_terminal_period" => ("No Trailing Period (Semi-Formal)", "[SEMI]"),
                    "no_punctuation" => ("No Punctuation", "[NONE]"),
                    "lowercase_no_punctuation" => {
                        ("Lowercase Without Punctuation", "[LOWER]")
                    }
                    _ => ("Full Punctuation", "[FULL]"),
                };
                (desc.to_string(), badge, Color::Cyan)
            }
            SettingKind::Theme => {
                let name = app.ui_theme.name();
                (format!("{} palette", name), "[PICKER]", c)
            }
            SettingKind::Microphone => {
                let dev = if app.active_device.len() > 30 {
                    format!("{}...", &app.active_device[..27])
                } else {
                    app.active_device.clone()
                };
                (dev, "[SELECT]", Color::Yellow)
            }
            SettingKind::ResetTerminal => (
                "Reset terminal state & clipboard bridge".to_string(),
                "[RUN]",
                Color::Yellow,
            ),
        }
    }
}

pub fn score_setting(query: &str, item: &SettingItem) -> Option<i32> {
    if query.is_empty() {
        return Some(0);
    }
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Some(0);
    }
    let title = item.title.to_lowercase();
    let kw = item.keywords.to_lowercase();

    // Exact title match
    if title == q {
        return Some(1000);
    }
    // Title starts with query
    if title.starts_with(&q) {
        return Some(800 - q.len() as i32);
    }
    // Title contains query
    if let Some(pos) = title.find(&q) {
        return Some(600 - pos as i32 * 10);
    }
    // Keywords contain query
    if let Some(pos) = kw.find(&q) {
        return Some(400 - pos as i32 * 5);
    }
    // Fuzzy subsequence in title
    if let Some(dist) = fuzzy_subsequence(&q, &title) {
        return Some(200 - dist as i32);
    }
    // Fuzzy subsequence in keywords
    if let Some(dist) = fuzzy_subsequence(&q, &kw) {
        return Some(100 - dist as i32);
    }
    None
}

fn fuzzy_subsequence(pattern: &str, target: &str) -> Option<usize> {
    let mut p_chars = pattern.chars().peekable();
    let mut dist = 0;
    for (i, c) in target.chars().enumerate() {
        if let Some(&p) = p_chars.peek() {
            if c == p {
                p_chars.next();
                dist += i;
            }
        }
    }
    if p_chars.peek().is_none() {
        Some(dist)
    } else {
        None
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SettingsPickerAction {
    Toggle(SettingKind),
    Close,
    Continue,
}

pub struct SettingsPickerState {
    pub query: String,
    pub selected_index: usize,
    pub filtered_indices: Vec<usize>,
}

impl SettingsPickerState {
    pub fn new() -> Self {
        Self {
            query: String::new(),
            selected_index: 0,
            filtered_indices: (0..SETTINGS.len()).collect(),
        }
    }

    pub fn update_filter(&mut self) {
        let mut scored: Vec<(usize, i32)> = Vec::new();
        for (i, item) in SETTINGS.iter().enumerate() {
            if let Some(score) = score_setting(&self.query, item) {
                scored.push((i, score));
            }
        }
        scored.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        self.filtered_indices = scored.into_iter().map(|(idx, _)| idx).collect();
        if self.selected_index >= self.filtered_indices.len() {
            self.selected_index = self.filtered_indices.len().saturating_sub(1);
        }
    }

    pub fn move_up(&mut self) {
        if self.selected_index > 0 {
            self.selected_index -= 1;
        } else if !self.filtered_indices.is_empty() {
            self.selected_index = self.filtered_indices.len() - 1;
        }
    }

    pub fn move_down(&mut self) {
        if !self.filtered_indices.is_empty() {
            if self.selected_index + 1 < self.filtered_indices.len() {
                self.selected_index += 1;
            } else {
                self.selected_index = 0;
            }
        }
    }

    pub fn handle_key(&mut self, key: KeyEvent) -> SettingsPickerAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('c') => return SettingsPickerAction::Close,
                KeyCode::Char('p') | KeyCode::Char('k') => {
                    self.move_up();
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Char('n') | KeyCode::Char('j') => {
                    self.move_down();
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Char('u') => {
                    self.query.clear();
                    self.update_filter();
                    return SettingsPickerAction::Continue;
                }
                _ => {}
            }
        }

        match key.code {
            KeyCode::Esc => SettingsPickerAction::Close,
            KeyCode::Enter => {
                if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                    SettingsPickerAction::Toggle(SETTINGS[idx].kind)
                } else {
                    SettingsPickerAction::Close
                }
            }
            KeyCode::Char(' ') => {
                // If search query is empty, Space toggles the selected setting!
                // If user is actively typing a multi-word search, space appends to query.
                if self.query.is_empty() {
                    if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                        return SettingsPickerAction::Toggle(SETTINGS[idx].kind);
                    }
                }
                self.query.push(' ');
                self.update_filter();
                SettingsPickerAction::Continue
            }
            KeyCode::Up => {
                self.move_up();
                SettingsPickerAction::Continue
            }
            KeyCode::Down | KeyCode::Tab => {
                self.move_down();
                SettingsPickerAction::Continue
            }
            KeyCode::BackTab => {
                self.move_up();
                SettingsPickerAction::Continue
            }
            KeyCode::PageUp => {
                self.selected_index = 0;
                SettingsPickerAction::Continue
            }
            KeyCode::PageDown => {
                if !self.filtered_indices.is_empty() {
                    self.selected_index = self.filtered_indices.len() - 1;
                }
                SettingsPickerAction::Continue
            }
            KeyCode::Backspace => {
                self.query.pop();
                self.update_filter();
                SettingsPickerAction::Continue
            }
            KeyCode::Char(c) => {
                self.query.push(c);
                self.update_filter();
                self.selected_index = 0;
                SettingsPickerAction::Continue
            }
            _ => SettingsPickerAction::Continue,
        }
    }
}

pub fn render_settings_picker(frame: &mut Frame, state: &SettingsPickerState, app: &App) {
    let area = frame.area();
    let theme_color = app.effective_color().color();

    // Centered popup modal
    let popup_w = 72u16.min(area.width.saturating_sub(4)).max(34);
    let popup_h = 18u16.min(area.height.saturating_sub(2)).max(12);
    let x = (area.width.saturating_sub(popup_w)) / 2;
    let y = (area.height.saturating_sub(popup_h)) / 2;
    let popup_area = Rect {
        x,
        y,
        width: popup_w,
        height: popup_h,
    };

    frame.render_widget(Clear, area);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(theme_color).add_modifier(Modifier::BOLD))
        .title(Span::styled(
            " ⚙️  Settings & Configuration ",
            Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
        ));

    let inner = block.inner(popup_area);
    frame.render_widget(block, popup_area);

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // Search input
            Constraint::Length(1), // Divider
            Constraint::Min(6),    // Settings list
            Constraint::Length(1), // Bottom divider
            Constraint::Length(1), // Help footer
        ])
        .split(inner);

    // Search bar
    let mut search_spans = vec![
        Span::styled(" 🔍 ", Style::default().fg(theme_color)),
        Span::styled(
            "Filter: ",
            Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
        ),
    ];
    if state.query.is_empty() {
        search_spans.push(Span::styled(
            "type to search (e.g. space, num, mouse, punc, mode)...",
            Style::default().add_modifier(Modifier::DIM),
        ));
    } else {
        search_spans.push(Span::styled(
            &state.query,
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
        ));
        search_spans.push(Span::styled("█", Style::default().fg(theme_color)));
    }
    frame.render_widget(Paragraph::new(Line::from(search_spans)), chunks[0]);

    // Top divider
    let sep_line = Span::styled(
        "─".repeat(inner.width as usize),
        Style::default().fg(Color::DarkGray),
    );
    frame.render_widget(Paragraph::new(Line::from(vec![sep_line])), chunks[1]);

    // Settings list
    let list_area = chunks[2];
    if state.filtered_indices.is_empty() {
        let no_match = Paragraph::new(Line::from(vec![
            Span::styled("  No settings match '", Style::default().fg(Color::DarkGray)),
            Span::styled(&state.query, Style::default().fg(Color::Yellow)),
            Span::styled("'. Press Backspace or Esc.", Style::default().fg(Color::DarkGray)),
        ]));
        frame.render_widget(no_match, list_area);
    } else {
        let max_visible = list_area.height as usize;
        let selected = state.selected_index;
        let scroll_offset = if selected >= max_visible {
            selected - max_visible + 1
        } else {
            0
        };

        let mut list_lines: Vec<Line<'static>> = Vec::new();
        for (view_i, &opt_idx) in state
            .filtered_indices
            .iter()
            .skip(scroll_offset)
            .take(max_visible)
            .enumerate()
        {
            let actual_idx = scroll_offset + view_i;
            let is_selected = actual_idx == state.selected_index;
            let item = &SETTINGS[opt_idx];
            let (val_str, badge, badge_color) = item.value_and_badge(app);

            let mut line_spans = Vec::new();
            if is_selected {
                line_spans.push(Span::styled(
                    "❯ ",
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::raw("  "));
            }

    // Setting Icon (every icon is display width 2 + 1 space = 3 columns)
            line_spans.push(Span::styled(format!("{} ", item.icon), Style::default()));

            // Title (fixed 20 columns)
            let title_str = format!("{:<20}", item.title);
            if is_selected {
                line_spans.push(Span::styled(
                    title_str,
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::styled(
                    title_str,
                    Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
                ));
            }

            // Value description (space + remaining width up to badge)
            // Layout: 2 (pointer) + 3 (icon) + 20 (title) + 1 (gap) + desc_w + 10 (badge) = list_area.width
            let desc_w = (list_area.width as usize).saturating_sub(36);
            let desc_disp = if val_str.len() > desc_w {
                format!("{}...", &val_str[..desc_w.saturating_sub(3)])
            } else {
                val_str
            };
            line_spans.push(Span::styled(
                format!(" {:<width$}", desc_disp, width = desc_w),
                Style::default().add_modifier(Modifier::DIM),
            ));

            // Badge / action (uniform 10 columns pill)
            let badge_str = format!("{:^10}", badge);
            if is_selected {
                line_spans.push(Span::styled(
                    badge_str,
                    Style::default()
                        .fg(Color::Black)
                        .bg(badge_color)
                        .add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::styled(
                    badge_str,
                    Style::default().fg(badge_color),
                ));
            }

            list_lines.push(Line::from(line_spans));
        }
        frame.render_widget(Paragraph::new(list_lines), list_area);
    }

    // Bottom divider
    let bot_sep = Span::styled(
        "─".repeat(inner.width as usize),
        Style::default().fg(Color::DarkGray),
    );
    frame.render_widget(Paragraph::new(Line::from(vec![bot_sep])), chunks[3]);

    // Footer
    let footer = Line::from(vec![
        Span::styled(" [↑/↓] ", Style::default().fg(Color::Cyan)),
        Span::styled("Navigate   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Enter/Space] ", Style::default().fg(Color::Green)),
        Span::styled("Toggle/Action   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Type] ", Style::default().fg(Color::Cyan)),
        Span::styled("Filter   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Esc] ", Style::default().fg(Color::Red)),
        Span::styled("Done", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[4]);
}

/// Run the interactive settings picker on the alternate screen.
pub fn run_settings_picker(
    writer: Option<&UnixStream>,
    rx: Option<&mpsc::Receiver<IpcEvent>>,
    app: &mut App,
) -> io::Result<()> {
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen)?;
    crossterm::terminal::enable_raw_mode()?;

    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = ratatui::Terminal::new(backend)?;
    terminal.hide_cursor()?;
    terminal.clear()?;

    let mut state = SettingsPickerState::new();

    loop {
        // Drain incoming messages so socket stays clean
        if let Some(r) = rx {
            while let Ok(ev) = r.try_recv() {
                match ev {
                    IpcEvent::Wire(w) => match w {
                        Wire::State { state: s, sub } => {
                            app.update_state(crate::app::RunState::from_wire(&s), sub);
                        }
                        Wire::Vu { level } => {
                            app.update_vu(level);
                        }
                        Wire::Cfg {
                            mic,
                            secondary,
                            backend,
                            muted,
                            auto_type,
                            output_mode,
                            sound_theme,
                            ui_theme,
                            punctuation_mode,
                            trailing_space,
                            auto_punctuate,
                            number_digits,
                            middle_click_enabled,
                        } => {
                            app.apply_config(
                                mic,
                                secondary,
                                backend,
                                muted,
                                auto_type,
                                output_mode,
                                sound_theme,
                                ui_theme,
                                punctuation_mode,
                                trailing_space,
                                auto_punctuate,
                                number_digits,
                                middle_click_enabled,
                            );
                        }
                        _ => {}
                    },
                    IpcEvent::Closed => {
                        app.should_quit = true;
                        break;
                    }
                }
            }
        }

        terminal.draw(|f| render_settings_picker(f, &state, app))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key) {
                        SettingsPickerAction::Close => break,
                        SettingsPickerAction::Continue => {}
                        SettingsPickerAction::Toggle(kind) => {
                            match kind {
                                SettingKind::TrailingSpace => {
                                    app.trailing_space = !app.trailing_space;
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "toggle_trailing_space");
                                    }
                                }
                                SettingKind::AutoPunctuate => {
                                    app.auto_punctuate = !app.auto_punctuate;
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "toggle_auto_punctuate");
                                    }
                                }
                                SettingKind::NumberDigits => {
                                    app.number_digits = !app.number_digits;
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "toggle_numbers");
                                    }
                                }
                                SettingKind::MiddleClick => {
                                    app.middle_click_enabled = !app.middle_click_enabled;
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "toggle_middle_click");
                                    }
                                }
                                SettingKind::SoundMute => {
                                    app.is_muted = !app.is_muted;
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "toggle_mute");
                                    }
                                }
                                SettingKind::OutputMode => {
                                    app.output_mode = match app.output_mode.as_str() {
                                        "clipboard" => "type".to_string(),
                                        "type" => "type_fast".to_string(),
                                        "type_fast" => "paste".to_string(),
                                        "paste" => "paste_terminal".to_string(),
                                        _ => "clipboard".to_string(),
                                    };
                                    app.auto_type = app.output_mode == "type"
                                        || app.output_mode == "type_fast";
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "cycle_output_mode");
                                    }
                                }
                                SettingKind::PunctuationMode => {
                                    app.cycle_punctuation();
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "cycle_punctuation");
                                    }
                                }
                                SettingKind::Theme => {
                                    // Open nested theme picker modal
                                    let _ = crossterm::execute!(
                                        io::stdout(),
                                        crossterm::terminal::LeaveAlternateScreen
                                    );
                                    let _ = theme_picker::run_theme_picker(
                                        app.ui_theme,
                                        writer,
                                        rx,
                                        app,
                                    );
                                    let _ = crossterm::execute!(
                                        io::stdout(),
                                        crossterm::terminal::EnterAlternateScreen
                                    );
                                    crossterm::terminal::enable_raw_mode()?;
                                }
                                SettingKind::Microphone => {
                                    // Exit settings modal and trigger audio device selection
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "change_device");
                                    }
                                    break;
                                }
                                SettingKind::ResetTerminal => {
                                    if let Some(w) = writer {
                                        ipc::send_cmd(w, "reset_terminal");
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    let _ = crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{App, Theme};

    #[test]
    fn test_settings_fuzzy_matching() {
        let item_space = SETTINGS
            .iter()
            .find(|s| s.kind == SettingKind::TrailingSpace)
            .unwrap();
        let item_num = SETTINGS
            .iter()
            .find(|s| s.kind == SettingKind::NumberDigits)
            .unwrap();
        let item_mouse = SETTINGS
            .iter()
            .find(|s| s.kind == SettingKind::MiddleClick)
            .unwrap();
        let item_mute = SETTINGS
            .iter()
            .find(|s| s.kind == SettingKind::SoundMute)
            .unwrap();

        assert!(score_setting("space", item_space).is_some());
        assert!(score_setting("num", item_num).is_some());
        assert!(score_setting("mouse", item_mouse).is_some());
        assert!(score_setting("mute", item_mute).is_some());
        assert!(score_setting("xyzabc", item_space).is_none());
    }

    #[test]
    fn test_settings_state_navigation_and_toggle() {
        let app = App::new("1.0.3", Theme::Cyan);
        let mut state = SettingsPickerState::new();
        assert_eq!(state.filtered_indices.len(), SETTINGS.len());

        // Type 'space'
        state.handle_key(KeyEvent::new(KeyCode::Char('s'), KeyModifiers::NONE));
        state.handle_key(KeyEvent::new(KeyCode::Char('p'), KeyModifiers::NONE));
        state.handle_key(KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE));

        assert_eq!(state.query, "spa");
        let top_match = SETTINGS[state.filtered_indices[0]].kind;
        assert_eq!(top_match, SettingKind::TrailingSpace);

        // Press Enter to toggle
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(
            action,
            SettingsPickerAction::Toggle(SettingKind::TrailingSpace)
        );

        // Test value and badge display
        let (val, badge, _) = SETTINGS[0].value_and_badge(&app);
        assert!(!val.is_empty());
        assert!(!badge.is_empty());

        let state2 = SettingsPickerState::new();
        let backend = ratatui::backend::TestBackend::new(80, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| render_settings_picker(f, &state2, &app)).unwrap();
    }
}

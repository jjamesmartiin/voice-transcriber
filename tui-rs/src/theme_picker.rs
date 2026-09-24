//! Interactive color theme picker modal with live preview and fuzzy search.

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

use crate::app::{App, Theme};
use crate::ipc::{self, IpcEvent, Wire};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ThemeOption {
    pub theme: Theme,
    pub name: &'static str,
    pub label: &'static str,
    pub desc: &'static str,
}

pub const THEME_OPTIONS: [ThemeOption; 8] = [
    ThemeOption {
        theme: Theme::Auto,
        name: "auto",
        label: "Auto",
        desc: "Detect from system terminal accent color",
    },
    ThemeOption {
        theme: Theme::Green,
        name: "green",
        label: "Green",
        desc: "Classic emerald / forest green",
    },
    ThemeOption {
        theme: Theme::Cyan,
        name: "cyan",
        label: "Cyan",
        desc: "Cyber electric cyan / aqua",
    },
    ThemeOption {
        theme: Theme::Blue,
        name: "blue",
        label: "Blue",
        desc: "Ocean royal blue / cobalt",
    },
    ThemeOption {
        theme: Theme::Magenta,
        name: "magenta",
        label: "Magenta",
        desc: "Neon magenta / purple / violet",
    },
    ThemeOption {
        theme: Theme::Yellow,
        name: "yellow",
        label: "Yellow",
        desc: "Solar amber / gold / warm yellow",
    },
    ThemeOption {
        theme: Theme::Red,
        name: "red",
        label: "Red",
        desc: "Crimson / coral / bold red",
    },
    ThemeOption {
        theme: Theme::White,
        name: "white",
        label: "White",
        desc: "Monochrome / crisp clean white",
    },
];

/// Match a query against a theme option. Returns a score if matching (higher is better).
pub fn score_theme(query: &str, opt: &ThemeOption) -> Option<i32> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Some(0);
    }
    let name = opt.name.to_lowercase();
    let label = opt.label.to_lowercase();
    let desc = opt.desc.to_lowercase();

    // Exact name match
    if name == q {
        return Some(1000);
    }
    // Name starts with query
    if name.starts_with(&q) {
        return Some(800 - q.len() as i32);
    }
    // Label starts with query
    if label.starts_with(&q) {
        return Some(750 - q.len() as i32);
    }
    // Name contains query
    if let Some(pos) = name.find(&q) {
        return Some(600 - pos as i32 * 10);
    }
    // Label contains query
    if let Some(pos) = label.find(&q) {
        return Some(500 - pos as i32 * 10);
    }
    // Description contains query
    if let Some(pos) = desc.find(&q) {
        return Some(300 - pos as i32 * 5);
    }
    // Fuzzy subsequence in name
    if let Some(dist) = fuzzy_subsequence(&q, &name) {
        return Some(200 - dist as i32);
    }
    // Fuzzy subsequence in description
    if let Some(dist) = fuzzy_subsequence(&q, &desc) {
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
pub enum PickerAction {
    Select(Theme),
    Cancel,
    Continue,
}

pub struct PickerState {
    pub query: String,
    pub selected_index: usize,
    pub filtered_indices: Vec<usize>,
    pub current_theme: Theme,
}

impl PickerState {
    pub fn new(current: Theme) -> Self {
        let mut s = Self {
            query: String::new(),
            selected_index: 0,
            filtered_indices: (0..THEME_OPTIONS.len()).collect(),
            current_theme: current,
        };
        if let Some(pos) = THEME_OPTIONS.iter().position(|o| o.theme == current) {
            s.selected_index = pos;
        }
        s
    }

    pub fn update_filter(&mut self) {
        let mut scored: Vec<(usize, i32)> = Vec::new();
        for (i, opt) in THEME_OPTIONS.iter().enumerate() {
            if let Some(score) = score_theme(&self.query, opt) {
                scored.push((i, score));
            }
        }
        // Sort descending by score; on tie, preserve default list order
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

    pub fn handle_key(&mut self, key: KeyEvent) -> PickerAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('c') => return PickerAction::Cancel,
                KeyCode::Char('p') | KeyCode::Char('k') => {
                    self.move_up();
                    return PickerAction::Continue;
                }
                KeyCode::Char('n') | KeyCode::Char('j') => {
                    self.move_down();
                    return PickerAction::Continue;
                }
                KeyCode::Char('u') => {
                    self.query.clear();
                    self.update_filter();
                    return PickerAction::Continue;
                }
                _ => {}
            }
        }

        match key.code {
            KeyCode::Esc => PickerAction::Cancel,
            KeyCode::Enter => {
                if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                    PickerAction::Select(THEME_OPTIONS[idx].theme)
                } else {
                    PickerAction::Cancel
                }
            }
            KeyCode::Up => {
                self.move_up();
                PickerAction::Continue
            }
            KeyCode::Down | KeyCode::Tab => {
                self.move_down();
                PickerAction::Continue
            }
            KeyCode::BackTab => {
                self.move_up();
                PickerAction::Continue
            }
            KeyCode::PageUp => {
                self.selected_index = 0;
                PickerAction::Continue
            }
            KeyCode::PageDown => {
                if !self.filtered_indices.is_empty() {
                    self.selected_index = self.filtered_indices.len() - 1;
                }
                PickerAction::Continue
            }
            KeyCode::Backspace => {
                self.query.pop();
                self.update_filter();
                PickerAction::Continue
            }
            KeyCode::Char(c) => {
                self.query.push(c);
                self.update_filter();
                self.selected_index = 0;
                PickerAction::Continue
            }
            _ => PickerAction::Continue,
        }
    }

    pub fn active_theme_preview(&self) -> Theme {
        if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
            THEME_OPTIONS[idx].theme
        } else {
            self.current_theme
        }
    }
}

pub fn render_picker(frame: &mut Frame, state: &PickerState) {
    let area = frame.area();

    // Centered popup modal
    let popup_w = 66u16.min(area.width.saturating_sub(4)).max(30);
    let popup_h = 16u16.min(area.height.saturating_sub(2)).max(10);
    let x = (area.width.saturating_sub(popup_w)) / 2;
    let y = (area.height.saturating_sub(popup_h)) / 2;
    let popup_area = Rect {
        x,
        y,
        width: popup_w,
        height: popup_h,
    };

    frame.render_widget(Clear, popup_area);

    let preview_theme = state.active_theme_preview();
    let preview_color = preview_theme.color();

    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(preview_color).add_modifier(Modifier::BOLD))
        .title(Span::styled(
            " 🎨 Select Color Theme ",
            Style::default().fg(preview_color).add_modifier(Modifier::BOLD),
        ));

    let inner = block.inner(popup_area);
    frame.render_widget(block, popup_area);

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // Search input
            Constraint::Length(1), // Divider
            Constraint::Min(4),    // Theme list
            Constraint::Length(1), // Bottom divider
            Constraint::Length(1), // Help footer
        ])
        .split(inner);

    // Search bar
    let mut search_spans = vec![
        Span::styled(" 🔍 ", Style::default().fg(preview_color)),
        Span::styled(
            "Filter: ",
            Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
        ),
    ];
    if state.query.is_empty() {
        search_spans.push(Span::styled(
            "type to search (e.g. cyan, magenta)...",
            Style::default().add_modifier(Modifier::DIM),
        ));
    } else {
        search_spans.push(Span::styled(
            &state.query,
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
        ));
        search_spans.push(Span::styled("█", Style::default().fg(preview_color)));
    }
    frame.render_widget(Paragraph::new(Line::from(search_spans)), chunks[0]);

    // Top divider
    let sep_line = Span::styled(
        "─".repeat(inner.width as usize),
        Style::default().fg(Color::DarkGray),
    );
    frame.render_widget(Paragraph::new(Line::from(vec![sep_line])), chunks[1]);

    // Themes list
    let list_area = chunks[2];
    if state.filtered_indices.is_empty() {
        let no_match = Paragraph::new(Line::from(vec![
            Span::styled("  No themes match '", Style::default().fg(Color::DarkGray)),
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
            let opt = &THEME_OPTIONS[opt_idx];
            let opt_color = opt.theme.color();
            let is_current = opt.theme == state.current_theme;

            let mut line_spans = Vec::new();
            if is_selected {
                line_spans.push(Span::styled(
                    "❯ ",
                    Style::default().fg(opt_color).add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::raw("  "));
            }

            // Bullet in that theme's color
            line_spans.push(Span::styled("● ", Style::default().fg(opt_color)));

            // Name
            let name_str = format!("{:<8}", opt.name);
            if is_selected {
                line_spans.push(Span::styled(
                    name_str,
                    Style::default().fg(opt_color).add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::styled(name_str, Style::default().fg(Color::White)));
            }

            // Description
            let desc_w = (list_area.width as usize).saturating_sub(26);
            let desc_str = if opt.desc.len() > desc_w {
                format!("{}...", &opt.desc[..desc_w.saturating_sub(3)])
            } else {
                opt.desc.to_string()
            };
            line_spans.push(Span::styled(
                format!(" {:<width$}", desc_str, width = desc_w),
                Style::default().add_modifier(Modifier::DIM),
            ));

            // Selection badge
            if is_selected {
                line_spans.push(Span::styled(
                    " ↵ Select ",
                    Style::default()
                        .fg(Color::Black)
                        .bg(opt_color)
                        .add_modifier(Modifier::BOLD),
                ));
            } else if is_current {
                line_spans.push(Span::styled(
                    " [Active] ",
                    Style::default().fg(opt_color).add_modifier(Modifier::DIM),
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
        Span::styled("[Type] ", Style::default().fg(Color::Cyan)),
        Span::styled("Search   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Enter] ", Style::default().fg(Color::Green)),
        Span::styled("Apply   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Esc] ", Style::default().fg(Color::Red)),
        Span::styled("Cancel", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[4]);
}

/// Run the interactive theme picker on the alternate screen.
pub fn run_theme_picker(
    current_theme: Theme,
    writer: Option<&UnixStream>,
    rx: Option<&mpsc::Receiver<IpcEvent>>,
    app: &mut App,
) -> io::Result<Option<Theme>> {
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen)?;
    crossterm::terminal::enable_raw_mode()?;

    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = ratatui::Terminal::new(backend)?;
    terminal.hide_cursor()?;

    let mut state = PickerState::new(current_theme);
    let result: Option<Theme>;

    loop {
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

        terminal.draw(|f| render_picker(f, &state))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key) {
                        PickerAction::Select(theme) => {
                            result = Some(theme);
                            break;
                        }
                        PickerAction::Cancel => {
                            result = None;
                            break;
                        }
                        PickerAction::Continue => {}
                    }
                }
            }
        }
    }

    let _ = crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen);

    if let Some(theme) = result {
        app.ui_theme = theme;
        if let Some(w) = writer {
            ipc::send_cmd_value(w, "set_theme", "theme", theme.name());
        }
    }

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_picker_fuzzy_matching() {
        let opt_cyan = THEME_OPTIONS.iter().find(|o| o.theme == Theme::Cyan).unwrap();
        let opt_mag = THEME_OPTIONS.iter().find(|o| o.theme == Theme::Magenta).unwrap();
        let opt_green = THEME_OPTIONS.iter().find(|o| o.theme == Theme::Green).unwrap();

        // Exact match
        assert!(score_theme("cyan", opt_cyan).unwrap() > 900);

        // Prefix match
        assert!(score_theme("cy", opt_cyan).is_some());
        assert!(score_theme("mag", opt_mag).is_some());

        // Single letter match
        let score_c_cyan = score_theme("c", opt_cyan).unwrap();
        let score_c_green = score_theme("c", opt_green).unwrap_or(0);
        assert!(score_c_cyan > score_c_green); // Cyan starts with 'c', Green only has 'c' in description

        // Non-match
        assert!(score_theme("xyz", opt_cyan).is_none());
    }

    #[test]
    fn test_picker_state_navigation_and_filtering() {
        let mut state = PickerState::new(Theme::Cyan);
        assert_eq!(state.filtered_indices.len(), 8);

        // Type 'mag'
        state.handle_key(KeyEvent::new(KeyCode::Char('m'), KeyModifiers::NONE));
        state.handle_key(KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE));
        state.handle_key(KeyEvent::new(KeyCode::Char('g'), KeyModifiers::NONE));

        assert_eq!(state.query, "mag");
        assert!(!state.filtered_indices.is_empty());
        let top_match = THEME_OPTIONS[state.filtered_indices[0]].theme;
        assert_eq!(top_match, Theme::Magenta);

        // Press Enter to select
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(action, PickerAction::Select(Theme::Magenta));

        // Test Backspace
        state.handle_key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));
        assert_eq!(state.query, "ma");

        // Test Esc
        let action_esc = state.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert_eq!(action_esc, PickerAction::Cancel);
    }
}

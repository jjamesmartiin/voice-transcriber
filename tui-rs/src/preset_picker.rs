//! Interactive mode preset switcher modal with live preview and fuzzy search.

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
use crate::textfit;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PresetOption {
    pub canon_id: &'static str,
    pub name: &'static str,
    pub badge: &'static str,
    pub desc: &'static str,
    pub preview: &'static str,
    pub color: Color,
    pub keywords: &'static str,
}

pub const PRESET_OPTIONS: [PresetOption; 5] = [
    PresetOption {
        canon_id: "full",
        name: "Default (Standard)",
        badge: "[DEFAULT]",
        desc: "Full punctuation, standard capitalization, and grammar rules",
        preview: "\"Hey, how are you? I'm good.\"",
        color: Color::Green,
        keywords: "default standard full punctuation capitalization grammar formal complete",
    },
    PresetOption {
        canon_id: "no_terminal_period",
        name: "Casual (No Ending Period)",
        badge: "[CASUAL]",
        desc: "Standard capitalization and commas, but no period at the end",
        preview: "\"Hey, how are you? I'm good\"",
        color: Color::Yellow,
        keywords: "casual semi formal no terminal period ending ending period chat slack message",
    },
    PresetOption {
        canon_id: "no_punctuation",
        name: "Autocorrect (Phone Style)",
        badge: "[PHONE]",
        desc: "Capitalize sentences and I'm, but strip commas and periods",
        preview: "\"Hey how are you I'm good\"",
        color: Color::Cyan,
        keywords: "autocorrect phone style none no punctuation mobile text texting",
    },
    PresetOption {
        canon_id: "aesthetic_lowercase",
        name: "Aesthetic Lowercase",
        badge: "[AESTH]",
        desc: "All lowercase with commas and questions, but no trailing period",
        preview: "\"hey, how are you? i'm good\"",
        color: Color::Magenta,
        keywords: "aesthetic lowercase soft style commas questions lower",
    },
    PresetOption {
        canon_id: "gen_z",
        name: "Pure Gen Z",
        badge: "[GEN Z]",
        desc: "All lowercase, zero punctuation, zero grammar enforcement",
        preview: "\"hey how are you i'm good\"",
        color: Color::Blue,
        keywords: "pure gen z genz lowercase no punctuation fast raw minimal chat",
    },
];

pub fn canonical_preset_id(mode: &str) -> &'static str {
    match mode.trim().to_lowercase().replace("-", "_").as_str() {
        "full" | "default" | "standard" => "full",
        "no_terminal_period" | "casual" | "semi_formal" | "no_period" | "no_ending_period" => {
            "no_terminal_period"
        }
        "no_punctuation" | "autocorrect" | "phone" | "none" | "no_punct" => "no_punctuation",
        "aesthetic_lowercase" | "aesthetic" | "lowercase_punct" | "lower_punct" => {
            "aesthetic_lowercase"
        }
        "gen_z" | "genz" | "pure_gen_z" | "lowercase_no_punctuation" | "lowercase_no_punct" => {
            "gen_z"
        }
        _ => "full",
    }
}

pub fn score_preset(query: &str, opt: &PresetOption) -> Option<i32> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Some(0);
    }
    let canon = opt.canon_id.to_lowercase();
    let name = opt.name.to_lowercase();
    let badge = opt.badge.to_lowercase();
    let desc = opt.desc.to_lowercase();
    let keywords = opt.keywords.to_lowercase();

    if canon == q || name == q {
        return Some(1000);
    }
    if canon.starts_with(&q) || name.starts_with(&q) {
        return Some(800 - q.len() as i32);
    }
    if badge.contains(&q) {
        return Some(700);
    }
    if let Some(pos) = name.find(&q) {
        return Some(600 - pos as i32 * 10);
    }
    if let Some(pos) = keywords.find(&q) {
        return Some(400 - pos as i32 * 5);
    }
    if let Some(pos) = desc.find(&q) {
        return Some(300 - pos as i32 * 5);
    }
    if let Some(dist) = fuzzy_subsequence(&q, &name) {
        return Some(200 - dist as i32);
    }
    if let Some(dist) = fuzzy_subsequence(&q, &keywords) {
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
pub enum PresetPickerAction {
    Select(&'static str),
    Cancel,
    Continue,
}

pub struct PresetPickerState {
    pub query: String,
    pub selected_index: usize,
    pub filtered_indices: Vec<usize>,
    pub current_canon: &'static str,
}

impl PresetPickerState {
    pub fn new(current_mode: &str) -> Self {
        let canon = canonical_preset_id(current_mode);
        let start_idx = PRESET_OPTIONS
            .iter()
            .position(|o| o.canon_id == canon)
            .unwrap_or(0);

        Self {
            query: String::new(),
            selected_index: start_idx,
            filtered_indices: (0..PRESET_OPTIONS.len()).collect(),
            current_canon: canon,
        }
    }

    pub fn update_filter(&mut self) {
        let mut scored: Vec<(usize, i32)> = Vec::new();
        for (i, opt) in PRESET_OPTIONS.iter().enumerate() {
            if let Some(score) = score_preset(&self.query, opt) {
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

    pub fn handle_key(&mut self, key: KeyEvent) -> PresetPickerAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('c') => return PresetPickerAction::Cancel,
                KeyCode::Char('p') | KeyCode::Char('k') => {
                    self.move_up();
                    return PresetPickerAction::Continue;
                }
                KeyCode::Char('n') | KeyCode::Char('j') => {
                    self.move_down();
                    return PresetPickerAction::Continue;
                }
                KeyCode::Char('u') => {
                    self.query.clear();
                    self.update_filter();
                    return PresetPickerAction::Continue;
                }
                _ => {}
            }
        }

        match key.code {
            KeyCode::Esc => PresetPickerAction::Cancel,
            KeyCode::Enter => {
                if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                    PresetPickerAction::Select(PRESET_OPTIONS[idx].canon_id)
                } else {
                    PresetPickerAction::Cancel
                }
            }
            KeyCode::Char(' ') => {
                if self.query.is_empty() {
                    if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                        return PresetPickerAction::Select(PRESET_OPTIONS[idx].canon_id);
                    }
                }
                self.query.push(' ');
                self.update_filter();
                PresetPickerAction::Continue
            }
            KeyCode::Up => {
                self.move_up();
                PresetPickerAction::Continue
            }
            KeyCode::Down | KeyCode::Tab => {
                self.move_down();
                PresetPickerAction::Continue
            }
            KeyCode::BackTab => {
                self.move_up();
                PresetPickerAction::Continue
            }
            KeyCode::PageUp => {
                self.selected_index = 0;
                PresetPickerAction::Continue
            }
            KeyCode::PageDown => {
                if !self.filtered_indices.is_empty() {
                    self.selected_index = self.filtered_indices.len() - 1;
                }
                PresetPickerAction::Continue
            }
            KeyCode::Backspace => {
                self.query.pop();
                self.update_filter();
                PresetPickerAction::Continue
            }
            KeyCode::Char(c) => {
                self.query.push(c);
                self.update_filter();
                self.selected_index = 0;
                PresetPickerAction::Continue
            }
            _ => PresetPickerAction::Continue,
        }
    }
}

struct PresetRowLayout {
    name_w: usize,
    preview_w: usize,
}

impl PresetRowLayout {
    const NAME_W: usize = 27;
    const BADGE_W: usize = 10;
    const MARGIN: usize = 1;
    /// pointer(2) + bullet(2) + gap(1) + pill + right margin
    const FIXED: usize = 2 + 2 + 1 + Self::BADGE_W + Self::MARGIN;

    fn new(total: usize) -> Self {
        let flexible = total.saturating_sub(Self::FIXED);
        let name_w = Self::NAME_W.min(flexible);
        Self {
            name_w,
            preview_w: flexible.saturating_sub(name_w),
        }
    }
}

fn preset_row(
    opt: &PresetOption,
    is_selected: bool,
    is_current: bool,
    layout: &PresetRowLayout,
) -> Line<'static> {
    let opt_color = opt.color;
    let mut spans: Vec<Span<'static>> = Vec::new();

    // Pointer column.
    if is_selected {
        spans.push(Span::styled(
            "❯ ",
            Style::default().fg(opt_color).add_modifier(Modifier::BOLD),
        ));
    } else {
        spans.push(Span::raw("  "));
    }

    // Color bullet.
    spans.push(Span::styled("● ", Style::default().fg(opt_color)));

    // Name column.
    let name_style = if is_selected {
        Style::default().fg(opt_color).add_modifier(Modifier::BOLD)
    } else if is_current {
        Style::default().fg(Color::White).add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(Color::White)
    };
    spans.push(Span::styled(
        textfit::fit(opt.name, layout.name_w),
        name_style,
    ));

    // Preview column (leading gap + padded text).
    let preview_style = if is_selected {
        Style::default().fg(opt_color).add_modifier(Modifier::ITALIC)
    } else {
        Style::default().add_modifier(Modifier::DIM)
    };
    spans.push(Span::styled(
        format!(" {}", textfit::fit(opt.preview, layout.preview_w)),
        preview_style,
    ));

    // Action pill
    if is_selected {
        spans.push(Span::styled(
            textfit::center("↵ Select", PresetRowLayout::BADGE_W),
            Style::default()
                .fg(Color::Black)
                .bg(opt_color)
                .add_modifier(Modifier::BOLD),
        ));
    } else if is_current {
        spans.push(Span::styled(
            textfit::center("[Active]", PresetRowLayout::BADGE_W),
            Style::default().fg(opt_color).add_modifier(Modifier::DIM),
        ));
    } else {
        spans.push(Span::styled(
            textfit::center(opt.badge, PresetRowLayout::BADGE_W),
            Style::default().fg(Color::DarkGray),
        ));
    }

    Line::from(spans)
}

pub fn render_preset_picker(frame: &mut Frame, state: &PresetPickerState, app: &App) {
    let area = frame.area();
    let theme_color = app.effective_color().color();

    // Centered popup modal
    let popup_w = 76u16.min(area.width.saturating_sub(4)).max(36);
    let popup_h = 15u16.min(area.height.saturating_sub(2)).max(10);
    let x = (area.width.saturating_sub(popup_w)) / 2;
    let y = (area.height.saturating_sub(popup_h)) / 2;
    let popup_area = Rect {
        x,
        y,
        width: popup_w,
        height: popup_h,
    };

    frame.render_widget(Clear, popup_area);

    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(theme_color).add_modifier(Modifier::BOLD))
        .title(Span::styled(
            " ✨ Select Mode Preset ",
            Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
        ));

    // Paragraph never clips to the frame buffer, so the inner rect has to be
    // intersected here or the list writes past the last row (a panic on
    // terminals shorter than the popup minimum).
    let inner = block.inner(popup_area.intersection(area));
    frame.render_widget(block, popup_area);

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // Search input
            Constraint::Length(1), // Divider
            Constraint::Min(5),    // Preset list
            Constraint::Length(1), // Bottom divider
            Constraint::Length(1), // Help footer
        ])
        .split(inner);

    // Search bar
    let mut search_spans = vec![
        Span::styled(" ✨ ", Style::default().fg(theme_color)),
        Span::styled(
            "Filter: ",
            Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
        ),
    ];
    if state.query.is_empty() {
        search_spans.push(Span::styled(
            "type to search (e.g. casual, phone, gen z, default)...",
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

    // Preset list
    let list_area = chunks[2];
    if state.filtered_indices.is_empty() {
        let no_match = Paragraph::new(Line::from(vec![
            Span::styled("  No presets match '", Style::default().fg(Color::DarkGray)),
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
        let layout = PresetRowLayout::new(list_area.width as usize);
        for (view_i, &opt_idx) in state
            .filtered_indices
            .iter()
            .enumerate()
            .skip(scroll_offset)
            .take(max_visible)
        {
            let opt = &PRESET_OPTIONS[opt_idx];
            let is_selected = view_i == state.selected_index;
            let is_current = opt.canon_id == state.current_canon;
            list_lines.push(preset_row(opt, is_selected, is_current, &layout));
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
        Span::styled("Select   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Type] ", Style::default().fg(Color::Cyan)),
        Span::styled("Filter   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Esc] ", Style::default().fg(Color::Red)),
        Span::styled("Cancel", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[4]);
}

pub fn run_preset_picker(
    writer: Option<&UnixStream>,
    rx: Option<&mpsc::Receiver<IpcEvent>>,
    app: &mut App,
) -> io::Result<Option<&'static str>> {
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen)?;
    crossterm::terminal::enable_raw_mode()?;

    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = ratatui::Terminal::new(backend)?;
    terminal.hide_cursor()?;

    let mut state = PresetPickerState::new(&app.punctuation_mode);
    let result: Option<&'static str>;

    loop {
        if let Some(r) = rx {
            while let Ok(ev) = r.try_recv() {
                match ev {
                    IpcEvent::Wire(w) => match w {
                        Wire::State { state: s, sub } => {
                            app.update_state(crate::app::RunState::from_wire(&s), sub);
                        }
                        Wire::Vu { level, levels } => {
                            app.apply_vu_wire(level, &levels);
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
                            number_mode,
                            serial_collapse,
                            spell_command,
                            middle_click_enabled,
                            typing_wpm,
                            hotkeys,
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
                                number_mode,
                                serial_collapse,
                                spell_command,
                                middle_click_enabled,
                                typing_wpm,
                                hotkeys,
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

        terminal.draw(|f| render_preset_picker(f, &state, app))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key) {
                        PresetPickerAction::Select(canon) => {
                            result = Some(canon);
                            break;
                        }
                        PresetPickerAction::Cancel => {
                            result = None;
                            break;
                        }
                        PresetPickerAction::Continue => {}
                    }
                }
            }
        }
    }

    let _ = crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen);

    if let Some(canon) = result {
        app.punctuation_mode = canon.to_string();
        if let Some(w) = writer {
            ipc::send_cmd_value(w, "set_punctuation", "mode", canon);
        }
    }

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::Theme;

    #[test]
    fn test_preset_picker_fuzzy_matching() {
        let opt_casual = PRESET_OPTIONS
            .iter()
            .find(|o| o.canon_id == "no_terminal_period")
            .unwrap();
        let opt_phone = PRESET_OPTIONS
            .iter()
            .find(|o| o.canon_id == "no_punctuation")
            .unwrap();

        assert!(score_preset("casual", opt_casual).is_some());
        assert!(score_preset("phone", opt_phone).is_some());
        assert!(score_preset("autocorrect", opt_phone).is_some());
        assert!(score_preset("xyz12345", opt_casual).is_none());
    }

    #[test]
    fn test_preset_picker_navigation_and_selection() {
        let app = App::new("1.1.1", Theme::Cyan);
        let mut state = PresetPickerState::new("full");
        assert_eq!(state.filtered_indices.len(), PRESET_OPTIONS.len());
        assert_eq!(state.selected_index, 0);

        // Move down to casual
        state.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert_eq!(state.selected_index, 1);

        // Press Enter to select
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(action, PresetPickerAction::Select("no_terminal_period"));

        // Render test
        let backend = ratatui::backend::TestBackend::new(80, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| render_preset_picker(f, &state, &app)).unwrap();
    }

    #[test]
    fn test_preset_picker_cancel() {
        let mut state = PresetPickerState::new("full");
        let action = state.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert_eq!(action, PresetPickerAction::Cancel);
    }

    #[test]
    fn test_canonical_preset_id() {
        assert_eq!(canonical_preset_id("default"), "full");
        assert_eq!(canonical_preset_id("casual"), "no_terminal_period");
        assert_eq!(canonical_preset_id("autocorrect"), "no_punctuation");
        assert_eq!(canonical_preset_id("aesthetic"), "aesthetic_lowercase");
        assert_eq!(canonical_preset_id("gen_z"), "gen_z");
        assert_eq!(canonical_preset_id("unknown_mode"), "full");
    }
}

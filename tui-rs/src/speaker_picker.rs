//! Speaker-name editor modal for the meeting screen.
//!
//! The speakers map is transcript **metadata**, not app config, so it is edited
//! from the meeting screen's `s` key rather than from the settings modal — see
//! `docs/meeting_mode.md`. The list is ordered: entry 1 names speaker 1, entry 2
//! names speaker 2, and a slot left unnamed falls back to `Speaker N` when the
//! transcript is rendered.
//!
//! The Python engine stays authoritative: this modal edits a local copy and
//! sends `set_speakers` with the whole ordered list on every change, and the
//! engine echoes the canonical list back in `cfg.speakers`.

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
use crate::ipc::{self, IpcEvent};

/// Runaway guard on a typed name; the engine does the real normalisation.
const MAX_NAME_LEN: usize = 80;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum InputMode {
    New,
    Rename(usize),
}

/// What the run loop should do after a keypress.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SpeakerPickerAction {
    /// The local list changed; the engine must be told the new order.
    Changed,
    Close,
    Continue,
}

pub struct SpeakerPickerState {
    /// The ordered names being edited. Index *i* names speaker *i*.
    pub names: Vec<String>,
    pub selected: usize,
    /// Active text prompt (`None` == list mode).
    input: Option<(InputMode, String)>,
}

impl SpeakerPickerState {
    pub fn new(names: Vec<String>) -> Self {
        Self {
            names,
            selected: 0,
            input: None,
        }
    }

    fn clamp(&mut self) {
        if self.names.is_empty() {
            self.selected = 0;
        } else if self.selected >= self.names.len() {
            self.selected = self.names.len() - 1;
        }
    }

    fn move_up(&mut self) {
        if self.names.is_empty() {
            self.selected = 0;
        } else if self.selected > 0 {
            self.selected -= 1;
        } else {
            self.selected = self.names.len() - 1;
        }
    }

    fn move_down(&mut self) {
        if self.names.is_empty() {
            self.selected = 0;
        } else if self.selected + 1 < self.names.len() {
            self.selected += 1;
        } else {
            self.selected = 0;
        }
    }

    /// Swap the selected entry with its neighbour; `false` when it is already
    /// at that end.
    fn move_entry(&mut self, delta: isize) -> bool {
        if self.names.is_empty() {
            return false;
        }
        let to = self.selected as isize + delta;
        if to < 0 || to >= self.names.len() as isize {
            return false;
        }
        let to = to as usize;
        self.names.swap(self.selected, to);
        self.selected = to;
        true
    }

    pub fn handle_key(&mut self, key: KeyEvent) -> SpeakerPickerAction {
        if self.input.is_some() {
            return self.handle_input_key(key);
        }
        self.handle_list_key(key)
    }

    fn handle_input_key(&mut self, key: KeyEvent) -> SpeakerPickerAction {
        let mode = self.input.as_ref().unwrap().0;
        match key.code {
            KeyCode::Esc => {
                self.input = None;
                SpeakerPickerAction::Continue
            }
            KeyCode::Enter => {
                let value = self.input.as_ref().unwrap().1.trim().to_string();
                self.input = None;
                match mode {
                    InputMode::New => {
                        if !value.is_empty() {
                            self.names.push(value);
                            self.selected = self.names.len() - 1;
                            return SpeakerPickerAction::Changed;
                        }
                    }
                    InputMode::Rename(idx) => {
                        if idx < self.names.len() {
                            if value.is_empty() {
                                // A blank rename clears the slot (mirrors
                                // `rename_speaker_name` + the delete in
                                // `t2.select_speaker_editor`).
                                self.names.remove(idx);
                            } else {
                                self.names[idx] = value;
                            }
                            self.clamp();
                            return SpeakerPickerAction::Changed;
                        }
                    }
                }
                SpeakerPickerAction::Continue
            }
            KeyCode::Backspace => {
                self.input.as_mut().unwrap().1.pop();
                SpeakerPickerAction::Continue
            }
            KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.input.as_mut().unwrap().1.clear();
                SpeakerPickerAction::Continue
            }
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.input = None;
                SpeakerPickerAction::Continue
            }
            KeyCode::Char(c) if !c.is_control() => {
                let buffer = &mut self.input.as_mut().unwrap().1;
                if buffer.chars().count() < MAX_NAME_LEN {
                    buffer.push(c);
                }
                SpeakerPickerAction::Continue
            }
            _ => SpeakerPickerAction::Continue,
        }
    }

    fn handle_list_key(&mut self, key: KeyEvent) -> SpeakerPickerAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('c') => return SpeakerPickerAction::Close,
                KeyCode::Char('j') => {
                    return if self.move_entry(1) {
                        SpeakerPickerAction::Changed
                    } else {
                        SpeakerPickerAction::Continue
                    }
                }
                KeyCode::Char('k') => {
                    return if self.move_entry(-1) {
                        SpeakerPickerAction::Changed
                    } else {
                        SpeakerPickerAction::Continue
                    }
                }
                _ => {}
            }
        }
        match key.code {
            // Save (and cancel) both keep the edits: they are applied live.
            KeyCode::Esc | KeyCode::Char('s') | KeyCode::Char('S') | KeyCode::Char('q') => {
                SpeakerPickerAction::Close
            }
            KeyCode::Up | KeyCode::Char('k') => {
                self.move_up();
                SpeakerPickerAction::Continue
            }
            KeyCode::Down | KeyCode::Char('j') => {
                self.move_down();
                SpeakerPickerAction::Continue
            }
            KeyCode::Char('J') => {
                if self.move_entry(1) {
                    SpeakerPickerAction::Changed
                } else {
                    SpeakerPickerAction::Continue
                }
            }
            KeyCode::Char('K') => {
                if self.move_entry(-1) {
                    SpeakerPickerAction::Changed
                } else {
                    SpeakerPickerAction::Continue
                }
            }
            KeyCode::Enter => {
                if let Some(name) = self.names.get(self.selected) {
                    self.input = Some((InputMode::Rename(self.selected), name.clone()));
                } else {
                    self.input = Some((InputMode::New, String::new()));
                }
                SpeakerPickerAction::Continue
            }
            KeyCode::Char('a') => {
                self.input = Some((InputMode::New, String::new()));
                SpeakerPickerAction::Continue
            }
            KeyCode::Char('d') => {
                if self.selected < self.names.len() {
                    self.names.remove(self.selected);
                    self.clamp();
                    return SpeakerPickerAction::Changed;
                }
                SpeakerPickerAction::Continue
            }
            _ => SpeakerPickerAction::Continue,
        }
    }
}

pub fn render_speaker_picker(frame: &mut Frame, state: &SpeakerPickerState, app: &App) {
    let area = frame.area();
    let theme_color = app.effective_color().color();

    let popup_w = 64u16.min(area.width.saturating_sub(4)).max(34).min(area.width);
    let popup_h = 16u16.min(area.height.saturating_sub(2)).max(8).min(area.height);
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
        .border_style(
            Style::default()
                .fg(theme_color)
                .add_modifier(Modifier::BOLD),
        )
        .title(Span::styled(
            " 🗣  Speakers for this meeting ",
            Style::default()
                .fg(theme_color)
                .add_modifier(Modifier::BOLD),
        ));

    let inner = block.inner(popup_area.intersection(area));
    frame.render_widget(block, popup_area);

    // A terminal shorter than the modal cannot show the detail rows; drawing
    // them would write past the buffer. Bail after the frame so tiny screens
    // show the titled box rather than a panic.
    if inner.height < 4 {
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // Intro
            Constraint::Min(3),    // List
            Constraint::Length(1), // Keys 1
            Constraint::Length(1), // Keys 2
        ])
        .split(inner);

    frame.render_widget(
        Paragraph::new(Line::from(vec![Span::styled(
            " Names apply in order; a slot left unnamed stays 'Speaker N'.",
            Style::default().add_modifier(Modifier::DIM),
        )])),
        chunks[0],
    );

    let list_area = chunks[1];
    if state.names.is_empty() {
        frame.render_widget(
            Paragraph::new(Line::from(vec![Span::styled(
                "  No speakers yet - press 'a' to add a name.",
                Style::default().add_modifier(Modifier::DIM),
            )])),
            list_area,
        );
    } else {
        let max_visible = (list_area.height as usize).max(1);
        let offset = if state.selected >= max_visible {
            state.selected - max_visible + 1
        } else {
            0
        };
        let mut lines: Vec<Line<'static>> = Vec::new();
        for (view_i, name) in state
            .names
            .iter()
            .skip(offset)
            .take(max_visible)
            .enumerate()
        {
            let idx = offset + view_i;
            let is_selected = idx == state.selected;
            let pointer = if is_selected { "❯ " } else { "  " };
            let name_style = if is_selected {
                Style::default()
                    .fg(theme_color)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            };
            lines.push(Line::from(vec![
                Span::styled(
                    pointer,
                    Style::default()
                        .fg(theme_color)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    format!("{:>2}. ", idx + 1),
                    Style::default().fg(Color::DarkGray),
                ),
                Span::styled(name.clone(), name_style),
            ]));
        }
        frame.render_widget(Paragraph::new(lines), list_area);
    }

    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(" [↑/↓ j/k] ", Style::default().fg(Color::Cyan)),
            Span::styled("Move   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[Enter] ", Style::default().fg(Color::Green)),
            Span::styled("Rename   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[a] ", Style::default().fg(Color::Green)),
            Span::styled("Add   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[d] ", Style::default().fg(Color::Red)),
            Span::styled("Delete", Style::default().add_modifier(Modifier::DIM)),
        ])),
        chunks[2],
    );
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(" [J/K] ", Style::default().fg(Color::Cyan)),
            Span::styled("Reorder   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[s/Esc] ", Style::default().fg(Color::Green)),
            Span::styled("Save   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[q] ", Style::default().fg(Color::Red)),
            Span::styled("Close", Style::default().add_modifier(Modifier::DIM)),
        ])),
        chunks[3],
    );

    if let Some((mode, buffer)) = &state.input {
        let prompt_w = 60u16.min(area.width.saturating_sub(4)).max(34);
        let prompt_h = 6u16.min(area.height.saturating_sub(2)).max(5);
        let px = (area.width.saturating_sub(prompt_w)) / 2;
        let py = (area.height.saturating_sub(prompt_h)) / 2;
        let prompt_area = Rect {
            x: px,
            y: py,
            width: prompt_w,
            height: prompt_h,
        };

        frame.render_widget(Clear, prompt_area);

        let title = match mode {
            InputMode::New => " New speaker name ".to_string(),
            InputMode::Rename(idx) => format!(" Rename speaker {} ", idx + 1),
        };
        let p_block = Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(
                Style::default()
                    .fg(theme_color)
                    .add_modifier(Modifier::BOLD),
            )
            .title(Span::styled(
                title,
                Style::default()
                    .fg(theme_color)
                    .add_modifier(Modifier::BOLD),
            ));
        let p_inner = p_block.inner(prompt_area.intersection(area));
        frame.render_widget(p_block, prompt_area);

        let p_chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(1), // Input
                Constraint::Length(1), // Divider
                Constraint::Length(1), // Footer
            ])
            .split(p_inner);

        let input_spans = vec![
            Span::styled(
                " ❯ ",
                Style::default()
                    .fg(theme_color)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                buffer.clone(),
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled("█", Style::default().fg(theme_color)),
        ];
        frame.render_widget(Paragraph::new(Line::from(input_spans)), p_chunks[0]);

        let sep = Line::from(vec![Span::styled(
            "─".repeat(p_inner.width as usize),
            Style::default().fg(Color::DarkGray),
        )]);
        frame.render_widget(Paragraph::new(sep), p_chunks[1]);

        frame.render_widget(
            Paragraph::new(Line::from(vec![
                Span::styled(" [Enter] ", Style::default().fg(Color::Green)),
                Span::styled("Save   ", Style::default().add_modifier(Modifier::DIM)),
                Span::styled("[Esc] ", Style::default().fg(Color::Red)),
                Span::styled("Cancel", Style::default().add_modifier(Modifier::DIM)),
            ])),
            p_chunks[2],
        );
    }
}

/// Run the interactive speaker-name editor on the alternate screen.
pub fn run_speaker_picker(
    writer: Option<&UnixStream>,
    rx: Option<&mpsc::Receiver<IpcEvent>>,
    app: &mut App,
) -> io::Result<()> {
    let mut terminal = crate::modal::enter_modal_screen()?;

    let mut state = SpeakerPickerState::new(app.speaker_names.clone());

    loop {
        // Drain incoming messages; `tx`/`ev` are queued by `handle_modal_wire`
        // for the main loop to flush once this modal returns.
        if let Some(r) = rx {
            while let Ok(ev) = r.try_recv() {
                match ev {
                    IpcEvent::Wire(w) => app.handle_modal_wire(*w),
                    IpcEvent::Closed => {
                        app.should_quit = true;
                        break;
                    }
                }
            }
        }
        // The inner `break` only leaves the drain loop; leave the modal too.
        if app.should_quit {
            break;
        }

        terminal.draw(|f| render_speaker_picker(f, &state, app))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key) {
                        SpeakerPickerAction::Close => break,
                        SpeakerPickerAction::Continue => {}
                        SpeakerPickerAction::Changed => {
                            app.speaker_names = state.names.clone();
                            ipc::fire_json(
                                writer,
                                "set_speakers",
                                serde_json::json!({ "speakers": &state.names }),
                                app,
                            );
                        }
                    }
                }
            }
        }
    }

    // The engine echoes the canonical list in `cfg`; in standalone mode there
    // is no engine, so keep the local copy so the screen stays consistent.
    app.speaker_names = state.names.clone();

    crate::modal::leave_modal_screen();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{App, Theme};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn state() -> SpeakerPickerState {
        SpeakerPickerState::new(vec!["Priya".to_string(), "Sam".to_string()])
    }

    #[test]
    fn test_render_short_and_normal_terminals() {
        let app = App::new("1.1.1", Theme::Cyan);
        for (w, h) in [(80, 24), (174, 3), (20, 2), (1, 1)] {
            let mut st = state();
            st.input = Some((InputMode::New, "Bo".to_string()));
            let backend = ratatui::backend::TestBackend::new(w, h);
            let mut terminal = ratatui::Terminal::new(backend).unwrap();
            terminal
                .draw(|f| render_speaker_picker(f, &st, &app))
                .unwrap_or_else(|e| panic!("{w}x{h} failed to draw: {e}"));
        }
    }

    #[test]
    fn test_add_appends_and_changes() {
        let mut st = state();
        assert_eq!(st.handle_key(key(KeyCode::Char('a'))), SpeakerPickerAction::Continue);
        assert!(st.input.is_some());
        for c in "Bo".chars() {
            st.handle_key(key(KeyCode::Char(c)));
        }
        assert_eq!(
            st.handle_key(key(KeyCode::Enter)),
            SpeakerPickerAction::Changed
        );
        assert_eq!(st.names, vec!["Priya", "Sam", "Bo"]);
        assert_eq!(st.selected, 2);
    }

    #[test]
    fn test_rename_replaces_and_blank_deletes() {
        let mut st = state();
        st.handle_key(key(KeyCode::Down));
        assert_eq!(st.handle_key(key(KeyCode::Enter)), SpeakerPickerAction::Continue);
        // Buffer is prefilled with the current name; clear it first.
        st.input.as_mut().unwrap().1.clear();
        for c in "Samuel".chars() {
            st.handle_key(key(KeyCode::Char(c)));
        }
        assert_eq!(
            st.handle_key(key(KeyCode::Enter)),
            SpeakerPickerAction::Changed
        );
        assert_eq!(st.names, vec!["Priya", "Samuel"]);

        // A blank rename deletes the slot (select the last entry explicitly,
        // since Down wraps around a two-item list).
        st.selected = 1;
        st.handle_key(key(KeyCode::Enter));
        st.input.as_mut().unwrap().1.clear();
        assert_eq!(
            st.handle_key(key(KeyCode::Enter)),
            SpeakerPickerAction::Changed
        );
        assert_eq!(st.names, vec!["Priya"]);
    }

    #[test]
    fn test_delete_and_reorder() {
        let mut st = state();
        assert_eq!(
            st.handle_key(key(KeyCode::Char('d'))),
            SpeakerPickerAction::Changed
        );
        assert_eq!(st.names, vec!["Sam"]);

        let mut st = SpeakerPickerState::new(vec![
            "A".to_string(),
            "B".to_string(),
            "C".to_string(),
        ]);
        assert_eq!(
            st.handle_key(key(KeyCode::Char('J'))),
            SpeakerPickerAction::Changed
        );
        assert_eq!(st.names, vec!["B", "A", "C"]);
        assert_eq!(st.selected, 1);
        assert_eq!(
            st.handle_key(key(KeyCode::Char('K'))),
            SpeakerPickerAction::Changed
        );
        assert_eq!(st.names, vec!["A", "B", "C"]);
    }

    #[test]
    fn test_escape_and_q_close() {
        let mut st = state();
        assert_eq!(st.handle_key(key(KeyCode::Esc)), SpeakerPickerAction::Close);
        assert_eq!(
            st.handle_key(key(KeyCode::Char('q'))),
            SpeakerPickerAction::Close
        );
        assert_eq!(
            st.handle_key(key(KeyCode::Char('s'))),
            SpeakerPickerAction::Close
        );
    }
}

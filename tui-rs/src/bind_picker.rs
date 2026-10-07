//! Push-to-talk bind picker modal: list, add and remove hotkey chords.
//!
//! Mirrors the nested-modal pattern used by the theme / preset / mic pickers:
//! `run_bind_picker` owns the alternate screen while it is up and is handed
//! control by the settings modal. The Python engine stays authoritative — this
//! module only sends `hotkey_add` / `hotkey_remove` and re-reads the canonical
//! chord list from the `cfg` payload the engine sends back.

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

/// Longest chord the prompt will accept, purely as a runaway guard. The engine
/// does the real validation and reports its own errors.
const MAX_CHORD_LEN: usize = 64;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BindPickerAction {
    Add(String),
    Remove(String),
    Close,
    Continue,
}

pub struct BindPickerState {
    pub selected_index: usize,
    /// Chord text buffer while the add prompt is open (`None` == list mode).
    pub chord_input: Option<String>,
}

impl BindPickerState {
    pub fn new() -> Self {
        Self {
            selected_index: 0,
            chord_input: None,
        }
    }

    /// Keep the cursor on a real row after the engine shortens the list.
    pub fn clamp(&mut self, len: usize) {
        if len == 0 {
            self.selected_index = 0;
        } else if self.selected_index >= len {
            self.selected_index = len - 1;
        }
    }

    pub fn move_up(&mut self, len: usize) {
        if len == 0 {
            self.selected_index = 0;
        } else if self.selected_index > 0 {
            self.selected_index -= 1;
        } else {
            self.selected_index = len - 1;
        }
    }

    pub fn move_down(&mut self, len: usize) {
        if len == 0 {
            self.selected_index = 0;
        } else if self.selected_index + 1 < len {
            self.selected_index += 1;
        } else {
            self.selected_index = 0;
        }
    }

    pub fn handle_key(&mut self, key: KeyEvent, binds: &[String]) -> BindPickerAction {
        // Text-input mode: same editing keys as the Typing WPM prompt.
        if let Some(ref mut input) = self.chord_input {
            match key.code {
                KeyCode::Esc => {
                    self.chord_input = None;
                    return BindPickerAction::Continue;
                }
                KeyCode::Enter => {
                    let chord = input.trim().to_string();
                    // An empty chord is never submitted; the engine validates
                    // everything else.
                    if chord.is_empty() {
                        return BindPickerAction::Continue;
                    }
                    self.chord_input = None;
                    return BindPickerAction::Add(chord);
                }
                KeyCode::Backspace => {
                    input.pop();
                    return BindPickerAction::Continue;
                }
                KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    input.clear();
                    return BindPickerAction::Continue;
                }
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    self.chord_input = None;
                    return BindPickerAction::Continue;
                }
                KeyCode::Char(c) if !c.is_control() => {
                    if input.chars().count() < MAX_CHORD_LEN {
                        input.push(c);
                    }
                    return BindPickerAction::Continue;
                }
                _ => return BindPickerAction::Continue,
            }
        }

        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('c') => return BindPickerAction::Close,
                KeyCode::Char('p') | KeyCode::Char('k') => {
                    self.move_up(binds.len());
                    return BindPickerAction::Continue;
                }
                KeyCode::Char('n') | KeyCode::Char('j') => {
                    self.move_down(binds.len());
                    return BindPickerAction::Continue;
                }
                _ => {}
            }
        }

        self.clamp(binds.len());
        match key.code {
            KeyCode::Esc => BindPickerAction::Close,
            KeyCode::Char('a') | KeyCode::Char('A') => {
                self.chord_input = Some(String::new());
                BindPickerAction::Continue
            }
            KeyCode::Char('d') | KeyCode::Char('D') | KeyCode::Delete => {
                match binds.get(self.selected_index) {
                    Some(chord) => BindPickerAction::Remove(chord.clone()),
                    None => BindPickerAction::Continue,
                }
            }
            KeyCode::Up => {
                self.move_up(binds.len());
                BindPickerAction::Continue
            }
            KeyCode::Down | KeyCode::Tab => {
                self.move_down(binds.len());
                BindPickerAction::Continue
            }
            KeyCode::BackTab => {
                self.move_up(binds.len());
                BindPickerAction::Continue
            }
            KeyCode::PageUp => {
                self.selected_index = 0;
                BindPickerAction::Continue
            }
            KeyCode::PageDown => {
                if !binds.is_empty() {
                    self.selected_index = binds.len() - 1;
                }
                BindPickerAction::Continue
            }
            _ => BindPickerAction::Continue,
        }
    }
}

pub fn render_bind_picker(frame: &mut Frame, state: &BindPickerState, app: &App) {
    let area = frame.area();
    let theme_color = app.effective_color().color();

    // Centered popup modal, both dimensions clamped to the terminal. A minimum
    // larger than the screen used to size the popup past it, and every layout
    // chunk inherited that oversized rect.
    let popup_w = 62u16
        .min(area.width.saturating_sub(4))
        .max(38)
        .min(area.width);
    let popup_h = 15u16
        .min(area.height.saturating_sub(2))
        .max(9)
        .min(area.height);
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
            " 🔑 Push-to-Talk Keys ",
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
            Constraint::Length(1), // Heading
            Constraint::Length(1), // Divider
            Constraint::Min(3),    // Bind list
            Constraint::Length(1), // Bottom divider
            Constraint::Length(1), // Help footer
        ])
        .split(inner);

    let heading = Line::from(vec![
        Span::styled(" ▸ ", Style::default().fg(theme_color)),
        Span::styled(
            "Hold these to dictate",
            Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
        ),
    ]);
    frame.render_widget(Paragraph::new(heading), chunks[0]);

    let sep_line = Span::styled(
        "─".repeat(inner.width as usize),
        Style::default().fg(Color::DarkGray),
    );
    frame.render_widget(Paragraph::new(Line::from(vec![sep_line])), chunks[1]);

    let list_area = chunks[2];
    if app.hotkeys.is_empty() {
        let empty = Paragraph::new(Line::from(vec![
            Span::styled("  No push-to-talk keys bound. ", Style::default().fg(Color::DarkGray)),
            Span::styled("Press 'a' to add one.", Style::default().fg(Color::Yellow)),
        ]));
        frame.render_widget(empty, list_area);
    } else {
        let max_visible = list_area.height as usize;
        let selected = state.selected_index.min(app.hotkeys.len() - 1);
        let scroll_offset = if max_visible > 0 && selected >= max_visible {
            selected - max_visible + 1
        } else {
            0
        };

        let mut list_lines: Vec<Line<'static>> = Vec::new();
        for (view_i, chord) in app
            .hotkeys
            .iter()
            .skip(scroll_offset)
            .take(max_visible)
            .enumerate()
        {
            let actual_idx = scroll_offset + view_i;
            let is_selected = actual_idx == selected;
            let (pointer, pointer_style) = if is_selected {
                (
                    " ❯ ",
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                )
            } else {
                ("   ", Style::default())
            };
            let chord_style = if is_selected {
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(Color::White)
            };
            list_lines.push(Line::from(vec![
                Span::styled(pointer, pointer_style),
                Span::styled(chord.clone(), chord_style),
            ]));
        }
        frame.render_widget(Paragraph::new(list_lines), list_area);
    }

    let bot_sep = Span::styled(
        "─".repeat(inner.width as usize),
        Style::default().fg(Color::DarkGray),
    );
    frame.render_widget(Paragraph::new(Line::from(vec![bot_sep])), chunks[3]);

    let footer = Line::from(vec![
        Span::styled("[a] ", Style::default().fg(Color::Green)),
        Span::styled("Add   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[d/Del] ", Style::default().fg(Color::Red)),
        Span::styled("Remove   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[↑/↓] ", Style::default().fg(Color::Cyan)),
        Span::styled("Navigate   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Esc] ", Style::default().fg(Color::Red)),
        Span::styled("Back", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[4]);

    if let Some(ref input) = state.chord_input {
        let prompt_w = 56u16
            .min(area.width.saturating_sub(4))
            .max(34)
            .min(area.width);
        let prompt_h = 7u16
            .min(area.height.saturating_sub(2))
            .max(5)
            .min(area.height);
        let px = (area.width.saturating_sub(prompt_w)) / 2;
        let py = (area.height.saturating_sub(prompt_h)) / 2;
        let prompt_area = Rect {
            x: px,
            y: py,
            width: prompt_w,
            height: prompt_h,
        };

        frame.render_widget(Clear, prompt_area);

        let p_block = Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme_color).add_modifier(Modifier::BOLD))
            .title(Span::styled(
                " 🔑 Add Push-to-Talk Key ",
                Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
            ));

        let p_inner = p_block.inner(prompt_area.intersection(area));
        frame.render_widget(p_block, prompt_area);

        let p_chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(1), // Prompt label
                Constraint::Length(1), // Divider
                Constraint::Length(1), // Input
                Constraint::Length(1), // Divider
                Constraint::Length(1), // Footer
            ])
            .split(p_inner);

        let prompt_label = Line::from(vec![Span::styled(
            " Type a chord (e.g. alt+shift, f13):",
            Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
        )]);
        frame.render_widget(Paragraph::new(prompt_label), p_chunks[0]);

        let sep1 = Line::from(vec![Span::styled(
            "─".repeat(p_inner.width as usize),
            Style::default().fg(Color::DarkGray),
        )]);
        frame.render_widget(Paragraph::new(sep1), p_chunks[1]);

        let input_spans = if input.is_empty() {
            vec![
                Span::styled(
                    " ❯ ",
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                ),
                Span::styled("alt+shift", Style::default().add_modifier(Modifier::DIM)),
                Span::styled("█", Style::default().fg(theme_color)),
                Span::styled(" example", Style::default().fg(Color::DarkGray)),
            ]
        } else {
            vec![
                Span::styled(
                    " ❯ ",
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    input.clone(),
                    Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
                ),
                Span::styled("█", Style::default().fg(theme_color)),
                Span::styled(" chord", Style::default().fg(Color::Cyan)),
            ]
        };
        frame.render_widget(Paragraph::new(Line::from(input_spans)), p_chunks[2]);

        let sep2 = Line::from(vec![Span::styled(
            "─".repeat(p_inner.width as usize),
            Style::default().fg(Color::DarkGray),
        )]);
        frame.render_widget(Paragraph::new(sep2), p_chunks[3]);

        let footer = Line::from(vec![
            Span::styled(" [Enter] ", Style::default().fg(Color::Green)),
            Span::styled("Add   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[Esc] ", Style::default().fg(Color::Red)),
            Span::styled("Cancel", Style::default().add_modifier(Modifier::DIM)),
        ]);
        frame.render_widget(Paragraph::new(footer), p_chunks[4]);
    }
}

/// Run the interactive push-to-talk bind picker on the alternate screen.
pub fn run_bind_picker(
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

    let mut state = BindPickerState::new();

    loop {
        // Drain incoming messages and apply the engine's authoritative chord
        // list as soon as it re-sends it. `tx`/`ev` are queued by
        // `handle_modal_wire` for the main loop to flush on our return.
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

        state.clamp(app.hotkeys.len());
        terminal.draw(|f| render_bind_picker(f, &state, app))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key, &app.hotkeys) {
                        BindPickerAction::Close => break,
                        BindPickerAction::Continue => {}
                        BindPickerAction::Add(chord) => {
                            if let Some(w) = writer {
                                if ipc::send_cmd_value(w, "hotkey_add", "chord", &chord).is_err() {
                                    app.should_quit = true;
                                }
                            }
                        }
                        BindPickerAction::Remove(chord) => {
                            if let Some(w) = writer {
                                if ipc::send_cmd_value(w, "hotkey_remove", "chord", &chord)
                                    .is_err()
                                {
                                    app.should_quit = true;
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
    use std::io::{BufRead, BufReader};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn chords() -> Vec<String> {
        vec!["alt+shift".to_string(), "f13".to_string()]
    }

    /// The list must render on a normal terminal and on one shorter than the
    /// popup's minimum without writing past the frame buffer.
    #[test]
    fn test_render_bind_picker_short_and_normal_terminals() {
        let app = App::new("1.1.1", Theme::Cyan);
        for (w, h) in [(80, 24), (174, 3), (20, 2), (1, 1)] {
            let mut state = BindPickerState::new();
            state.chord_input = Some("alt+shift".to_string());
            let backend = ratatui::backend::TestBackend::new(w, h);
            let mut terminal = ratatui::Terminal::new(backend).unwrap();
            terminal
                .draw(|f| render_bind_picker(f, &state, &app))
                .unwrap_or_else(|e| panic!("{w}x{h} failed to draw: {e}"));
        }
    }

    #[test]
    fn test_add_prompt_submits_typed_chord() {
        let binds = chords();
        let mut state = BindPickerState::new();

        // 'a' opens the chord prompt...
        assert_eq!(state.handle_key(key(KeyCode::Char('a')), &binds), BindPickerAction::Continue);
        assert!(state.chord_input.is_some());

        // ...and typing edits the buffer.
        for c in "alt+shift".chars() {
            state.handle_key(key(KeyCode::Char(c)), &binds);
        }
        assert_eq!(state.chord_input.as_deref(), Some("alt+shift"));

        // Enter emits Add with exactly the typed chord.
        assert_eq!(
            state.handle_key(key(KeyCode::Enter), &binds),
            BindPickerAction::Add("alt+shift".to_string())
        );
        assert!(state.chord_input.is_none());
    }

    #[test]
    fn test_empty_chord_is_not_submitted() {
        let binds = chords();
        let mut state = BindPickerState::new();
        state.handle_key(key(KeyCode::Char('a')), &binds);

        // Whitespace-only and truly empty input both stay in the prompt.
        assert_eq!(state.handle_key(key(KeyCode::Enter), &binds), BindPickerAction::Continue);
        assert!(state.chord_input.is_some());

        state.handle_key(key(KeyCode::Char(' ')), &binds);
        assert_eq!(state.handle_key(key(KeyCode::Enter), &binds), BindPickerAction::Continue);
        assert_eq!(state.chord_input.as_deref(), Some(" "));
    }

    #[test]
    fn test_remove_emits_selected_chord() {
        let binds = chords();
        let mut state = BindPickerState::new();

        // Second row.
        state.handle_key(key(KeyCode::Down), &binds);
        assert_eq!(state.selected_index, 1);
        assert_eq!(
            state.handle_key(key(KeyCode::Char('d')), &binds),
            BindPickerAction::Remove("f13".to_string())
        );

        // Delete is the same action on the first row.
        state.selected_index = 0;
        assert_eq!(
            state.handle_key(key(KeyCode::Delete), &binds),
            BindPickerAction::Remove("alt+shift".to_string())
        );
    }

    #[test]
    fn test_esc_closes_picker_and_prompt() {
        let binds = chords();
        let mut state = BindPickerState::new();

        // Esc in the add prompt only cancels the prompt.
        state.handle_key(key(KeyCode::Char('a')), &binds);
        state.handle_key(key(KeyCode::Char('x')), &binds);
        assert_eq!(state.handle_key(key(KeyCode::Esc), &binds), BindPickerAction::Continue);
        assert!(state.chord_input.is_none());

        // Esc in list mode returns to settings.
        assert_eq!(state.handle_key(key(KeyCode::Esc), &binds), BindPickerAction::Close);
    }

    #[test]
    fn test_remove_on_empty_list_is_a_noop() {
        let mut state = BindPickerState::new();
        assert_eq!(
            state.handle_key(key(KeyCode::Char('d')), &[]),
            BindPickerAction::Continue
        );
    }

    #[test]
    fn test_clamp_after_removal() {
        let binds = chords();
        let mut state = BindPickerState::new();
        state.selected_index = 1;
        state.clamp(1);
        assert_eq!(state.selected_index, 0);
        state.clamp(0);
        assert_eq!(state.selected_index, 0);
        let _ = binds;
    }

    /// End-to-end wire check: the action a user triggers is turned into the
    /// exact JSON command the engine expects, addressable by the engine.
    #[test]
    fn test_add_and_remove_emit_wire_commands() {
        let binds = chords();
        let mut state = BindPickerState::new();

        let (client, server) = UnixStream::pair().unwrap();
        let mut reader = BufReader::new(server);

        // `a` -> type -> Enter -> Add -> send `hotkey_add`.
        state.handle_key(key(KeyCode::Char('a')), &binds);
        for c in "rightctrl+shift".chars() {
            state.handle_key(key(KeyCode::Char(c)), &binds);
        }
        let action = state.handle_key(key(KeyCode::Enter), &binds);
        let chord = match action {
            BindPickerAction::Add(c) => c,
            other => panic!("expected Add, got {other:?}"),
        };
        ipc::send_cmd_value(&client, "hotkey_add", "chord", &chord).unwrap();

        let mut line = String::new();
        reader.read_line(&mut line).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_eq!(parsed["cmd"], "hotkey_add");
        assert_eq!(parsed["chord"], "rightctrl+shift");

        // `d` -> Remove -> send `hotkey_remove`.
        state.selected_index = 0;
        let action = state.handle_key(key(KeyCode::Char('d')), &binds);
        let chord = match action {
            BindPickerAction::Remove(c) => c,
            other => panic!("expected Remove, got {other:?}"),
        };
        ipc::send_cmd_value(&client, "hotkey_remove", "chord", &chord).unwrap();

        line.clear();
        reader.read_line(&mut line).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_eq!(parsed["cmd"], "hotkey_remove");
        assert_eq!(parsed["chord"], "alt+shift");
    }
}

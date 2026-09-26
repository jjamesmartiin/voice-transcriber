//! Interactive audio input device (microphone) picker modal with live audio levels and fuzzy search.

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
use crate::ipc::{self, AudioDeviceInfo, IpcEvent, Wire};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum MicPickerAction {
    Select(usize),
    Cancel,
    Continue,
}

pub struct MicPickerState {
    pub query: String,
    pub selected_index: usize,
    pub filtered_indices: Vec<usize>,
    pub last_monitored_device_idx: Option<usize>,
}

impl MicPickerState {
    pub fn new(device_count: usize) -> Self {
        Self {
            query: String::new(),
            selected_index: 0,
            filtered_indices: (0..device_count).collect(),
            last_monitored_device_idx: None,
        }
    }

    pub fn update_filter(&mut self, devices: &[AudioDeviceInfo]) {
        let q = self.query.trim().to_lowercase();
        if q.is_empty() {
            self.filtered_indices = (0..devices.len()).collect();
        } else {
            let mut scored: Vec<(usize, i32)> = Vec::new();
            for (i, dev) in devices.iter().enumerate() {
                let name = dev.name.to_lowercase();
                let disp = dev.display_name.as_deref().unwrap_or("").to_lowercase();
                if name == q || disp == q {
                    scored.push((i, 1000));
                } else if name.starts_with(&q) || disp.starts_with(&q) {
                    scored.push((i, 800 - q.len() as i32));
                } else if let Some(pos) = name.find(&q).or_else(|| disp.find(&q)) {
                    scored.push((i, 600 - pos as i32 * 10));
                } else if let Some(dist) = fuzzy_subsequence(&q, &name).or_else(|| fuzzy_subsequence(&q, &disp)) {
                    scored.push((i, 200 - dist as i32));
                }
            }
            scored.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
            self.filtered_indices = scored.into_iter().map(|(idx, _)| idx).collect();
        }
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

    pub fn handle_key(&mut self, key: KeyEvent, devices: &[AudioDeviceInfo]) -> MicPickerAction {
        if key.modifiers.contains(KeyModifiers::CONTROL) {
            match key.code {
                KeyCode::Char('c') => return MicPickerAction::Cancel,
                KeyCode::Char('p') | KeyCode::Char('k') => {
                    self.move_up();
                    return MicPickerAction::Continue;
                }
                KeyCode::Char('n') | KeyCode::Char('j') => {
                    self.move_down();
                    return MicPickerAction::Continue;
                }
                KeyCode::Char('u') => {
                    self.query.clear();
                    self.update_filter(devices);
                    return MicPickerAction::Continue;
                }
                _ => {}
            }
        }

        match key.code {
            KeyCode::Esc => MicPickerAction::Cancel,
            KeyCode::Enter => {
                if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                    MicPickerAction::Select(idx)
                } else {
                    MicPickerAction::Cancel
                }
            }
            KeyCode::Char(' ') => {
                if self.query.is_empty() {
                    if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                        return MicPickerAction::Select(idx);
                    }
                }
                self.query.push(' ');
                self.update_filter(devices);
                MicPickerAction::Continue
            }
            KeyCode::Up => {
                self.move_up();
                MicPickerAction::Continue
            }
            KeyCode::Down | KeyCode::Tab => {
                self.move_down();
                MicPickerAction::Continue
            }
            KeyCode::BackTab => {
                self.move_up();
                MicPickerAction::Continue
            }
            KeyCode::PageUp => {
                self.selected_index = 0;
                MicPickerAction::Continue
            }
            KeyCode::PageDown => {
                if !self.filtered_indices.is_empty() {
                    self.selected_index = self.filtered_indices.len() - 1;
                }
                MicPickerAction::Continue
            }
            KeyCode::Backspace => {
                self.query.pop();
                self.update_filter(devices);
                MicPickerAction::Continue
            }
            KeyCode::Char(c) => {
                self.query.push(c);
                self.update_filter(devices);
                self.selected_index = 0;
                MicPickerAction::Continue
            }
            _ => MicPickerAction::Continue,
        }
    }
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

/// Horizontal audio level bar graph.
pub fn vu_bar_spans(level: f32, bar_len: usize) -> Vec<Span<'static>> {
    let level_clamped = level.min(1.0).max(0.0);
    let pct = (level_clamped * 100.0) as i32;
    let filled_len = ((level_clamped * 3.0 * bar_len as f32) as usize).min(bar_len);
    let empty_len = bar_len.saturating_sub(filled_len);

    let green = Style::default().fg(Color::Green);
    let yellow = Style::default().fg(Color::Yellow);
    let red = Style::default().fg(Color::Red);
    let dim = Style::default().fg(Color::DarkGray);

    let mut spans = Vec::new();
    spans.push(Span::styled("[", dim));
    if filled_len > 0 {
        if filled_len > 9 {
            spans.push(Span::styled("█".repeat(6), green));
            spans.push(Span::styled("█".repeat(3), yellow));
            spans.push(Span::styled("█".repeat(filled_len - 9), red));
        } else if filled_len > 6 {
            spans.push(Span::styled("█".repeat(6), green));
            spans.push(Span::styled("█".repeat(filled_len - 6), yellow));
        } else {
            spans.push(Span::styled("█".repeat(filled_len), green));
        }
    }
    if empty_len > 0 {
        spans.push(Span::styled("░".repeat(empty_len), dim));
    }
    spans.push(Span::styled(
        format!("] {:>3}%", pct),
        if pct > 0 {
            Style::default().fg(Color::Cyan)
        } else {
            dim
        },
    ));
    spans
}

pub fn render_mic_picker(frame: &mut Frame, state: &MicPickerState, app: &App) {
    let area = frame.area();
    let theme_color = app.effective_color().color();

    // Centered popup modal
    let popup_w = 76u16.min(area.width.saturating_sub(4)).max(40);
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
            " 🎤 Audio Input Device (Microphone) ",
            Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
        ));

    let inner = block.inner(popup_area);
    frame.render_widget(block, popup_area);

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // Search input
            Constraint::Length(1), // Divider
            Constraint::Length(1), // Live monitor note / tip
            Constraint::Min(5),    // Device list
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
            "type to search (e.g. default, usb, quadcast)...",
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

    // Live audio level helper tip
    let tip = Line::from(vec![
        Span::styled("  💡 Live Monitor: ", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::styled("Speak or hold Alt+Shift to preview audio levels in real time", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(tip), chunks[2]);

    // Device list
    let list_area = chunks[3];
    let devices = &app.audio_devices;

    if state.filtered_indices.is_empty() {
        let no_match = Paragraph::new(Line::from(vec![
            Span::styled("  No devices match '", Style::default().fg(Color::DarkGray)),
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
            let dev = &devices[opt_idx];
            let is_active = dev.name.to_lowercase() == app.active_device.to_lowercase()
                || (dev.is_active && app.active_device.is_empty());

            let mut line_spans = Vec::new();
            if is_selected {
                line_spans.push(Span::styled(
                    "❯ ",
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::raw("  "));
            }

            // Mic Icon (display width 2 + 1 space = 3 columns)
            line_spans.push(Span::styled("🎤 ", Style::default()));

            // Name column
            // Total list_area.width = 2 (pointer) + 3 (icon) + name_w + 1 (gap) + 18 (vu) + 1 (gap) + 10 (badge)
            let fixed_w = 2 + 3 + 1 + 18 + 1 + 10;
            let name_w = (list_area.width as usize).saturating_sub(fixed_w).max(16);
            let title = dev.display_name.as_deref().unwrap_or(&dev.name);
            let name_disp = if title.chars().count() > name_w {
                let head: String = title.chars().take(name_w.saturating_sub(3)).collect();
                format!("{head}...")
            } else {
                title.to_string()
            };

            if is_selected {
                line_spans.push(Span::styled(
                    format!("{:<width$} ", name_disp, width = name_w),
                    Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
                ));
            } else if is_active {
                line_spans.push(Span::styled(
                    format!("{:<width$} ", name_disp, width = name_w),
                    Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::styled(
                    format!("{:<width$} ", name_disp, width = name_w),
                    Style::default().fg(Color::Gray),
                ));
            }

            // Live horizontal VU meter bar graph
            // Show real-time VU level for currently highlighted device or active device
            let vu_level = if is_selected || is_active {
                app.vu_level
            } else {
                0.0
            };
            line_spans.extend(vu_bar_spans(vu_level, 12));
            line_spans.push(Span::raw(" "));

            // Badge / action (uniform 10 columns pill)
            if is_active {
                line_spans.push(Span::styled(
                    format!("{:^10}", "[ACTIVE]"),
                    Style::default()
                        .fg(Color::Black)
                        .bg(Color::Green)
                        .add_modifier(Modifier::BOLD),
                ));
            } else if is_selected {
                line_spans.push(Span::styled(
                    format!("{:^10}", "↵ SELECT"),
                    Style::default()
                        .fg(Color::Black)
                        .bg(theme_color)
                        .add_modifier(Modifier::BOLD),
                ));
            } else {
                line_spans.push(Span::styled(
                    format!("{:^10}", ""),
                    Style::default().fg(Color::DarkGray),
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
    frame.render_widget(Paragraph::new(Line::from(vec![bot_sep])), chunks[4]);

    // Footer
    let footer = Line::from(vec![
        Span::styled(" [↑/↓] ", Style::default().fg(Color::Cyan)),
        Span::styled("Preview   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Enter/Space] ", Style::default().fg(Color::Green)),
        Span::styled("Select   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Type] ", Style::default().fg(Color::Cyan)),
        Span::styled("Filter   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Esc] ", Style::default().fg(Color::Red)),
        Span::styled("Cancel", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[5]);
}

/// Run the interactive mic picker on the alternate screen.
pub fn run_mic_picker(
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

    // Request fresh device list from Python backend
    if let Some(w) = writer {
        ipc::send_cmd(w, "get_devices");
        ipc::send_cmd(w, "start_mic_monitor");
    }

    let mut state = MicPickerState::new(app.audio_devices.len());
    // Position cursor on currently active device if possible
    if let Some(pos) = app
        .audio_devices
        .iter()
        .position(|d| d.name.to_lowercase() == app.active_device.to_lowercase())
    {
        state.selected_index = pos;
    }

    loop {
        if writer.is_none() {
            app.synthetic_vu();
        }

        // Drain incoming messages
        if let Some(r) = rx {
            while let Ok(ev) = r.try_recv() {
                match ev {
                    IpcEvent::Wire(w) => match w {
                        Wire::Devices { devices } => {
                            app.update_devices(devices);
                            state.update_filter(&app.audio_devices);
                        }
                        Wire::Vu { level } => {
                            app.update_vu(level);
                        }
                        Wire::State { state: s, sub } => {
                            app.update_state(crate::app::RunState::from_wire(&s), sub);
                        }
                        Wire::Cfg { mic, .. } => {
                            if let Some(m) = mic {
                                app.active_device = m;
                            }
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

        // If the highlighted device changed, switch live mic monitoring to it
        if let Some(&dev_idx) = state.filtered_indices.get(state.selected_index) {
            if let Some(dev) = app.audio_devices.get(dev_idx) {
                if state.last_monitored_device_idx != Some(dev.index) {
                    state.last_monitored_device_idx = Some(dev.index);
                    if let Some(w) = writer {
                        let line = format!(
                            "{{\"t\":\"cmd\",\"cmd\":\"start_mic_monitor\",\"index\":{}}}\n",
                            dev.index
                        );
                        let mut stream = w;
                        let _ = std::io::Write::write_all(&mut stream, line.as_bytes());
                        let _ = std::io::Write::flush(&mut stream);
                    }
                }
            }
        }

        terminal.draw(|f| render_mic_picker(f, &state, app))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key, &app.audio_devices) {
                        MicPickerAction::Cancel => break,
                        MicPickerAction::Continue => {}
                        MicPickerAction::Select(idx) => {
                            if let Some(dev) = app.audio_devices.get(idx) {
                                let dev_name = dev.name.clone();
                                let dev_index = dev.index;
                                app.active_device = dev_name.clone();
                                if let Some(w) = writer {
                                    let line = format!(
                                        "{{\"t\":\"cmd\",\"cmd\":\"set_device\",\"device\":\"{}\",\"index\":{}}}\n",
                                        dev_name, dev_index
                                    );
                                    let mut stream = w;
                                    let _ = std::io::Write::write_all(&mut stream, line.as_bytes());
                                    let _ = std::io::Write::flush(&mut stream);
                                }
                            }
                            break;
                        }
                    }
                }
            }
        }
    }

    // Stop live mic monitoring stream on exit
    if let Some(w) = writer {
        ipc::send_cmd(w, "stop_mic_monitor");
    }

    let _ = crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{App, Theme};

    #[test]
    fn test_mic_picker_filtering() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.audio_devices = vec![
            AudioDeviceInfo {
                index: 0,
                name: "HyperX QuadCast S".to_string(),
                display_name: Some("HyperX QuadCast S".to_string()),
                channels: 2,
                is_default: true,
                is_active: true,
            },
            AudioDeviceInfo {
                index: 1,
                name: "Built-in Analog Stereo".to_string(),
                display_name: Some("Built-in Analog Stereo".to_string()),
                channels: 2,
                is_default: false,
                is_active: false,
            },
            AudioDeviceInfo {
                index: 2,
                name: "Blue Yeti Microphone".to_string(),
                display_name: Some("Blue Yeti Microphone".to_string()),
                channels: 2,
                is_default: false,
                is_active: false,
            },
        ];

        let mut state = MicPickerState::new(app.audio_devices.len());
        assert_eq!(state.filtered_indices.len(), 3);

        // Filter by "quad"
        state.handle_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::NONE), &app.audio_devices);
        state.handle_key(KeyEvent::new(KeyCode::Char('u'), KeyModifiers::NONE), &app.audio_devices);
        state.handle_key(KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE), &app.audio_devices);

        assert_eq!(state.filtered_indices.len(), 1);
        assert_eq!(state.filtered_indices[0], 0);

        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), &app.audio_devices);
        assert_eq!(action, MicPickerAction::Select(0));
    }

    #[test]
    fn test_mic_picker_render() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.vu_level = 0.52;
        let state = MicPickerState::new(app.audio_devices.len());
        let backend = ratatui::backend::TestBackend::new(80, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal.draw(|f| render_mic_picker(f, &state, &app)).unwrap();
    }
}

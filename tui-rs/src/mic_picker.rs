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
use crate::textfit;

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
    /// Device indices the engine is currently metering. Mirrors the last set
    /// sent downstream, so the picker only re-commands when the visible rows
    /// actually change (scroll, filter, device list update).
    pub monitored: Vec<usize>,
}

impl MicPickerState {
    pub fn new(device_count: usize) -> Self {
        Self {
            query: String::new(),
            selected_index: 0,
            filtered_indices: (0..device_count).collect(),
            monitored: Vec::new(),
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

/// Width of the VU meter (bracket, bars, percent), as produced by
/// [`vu_bar_spans`] when called with `bar_len = VU_W - 7`.
pub const VU_W: usize = 18;

/// Modal box size for a terminal of this size. Shared by the renderer and the
/// monitor sync loop so the rows that get metered are exactly the rows shown.
const POPUP_MAX_W: u16 = 76;
const POPUP_MAX_H: u16 = 18;
const POPUP_MIN_W: u16 = 40;
const POPUP_MIN_H: u16 = 12;
/// Search line, tip line, two dividers and the footer.
const POPUP_CHROME: usize = 5;

fn popup_size(area_w: u16, area_h: u16) -> (u16, u16) {
    let w = POPUP_MAX_W.min(area_w.saturating_sub(4)).max(POPUP_MIN_W);
    let h = POPUP_MAX_H.min(area_h.saturating_sub(2)).max(POPUP_MIN_H);
    (w, h)
}

/// How many device rows the modal can show at once.
fn visible_rows(area_w: u16, area_h: u16) -> usize {
    let (_, h) = popup_size(area_w, area_h);
    (h as usize).saturating_sub(2 + POPUP_CHROME).max(1)
}

/// First position of `filtered_indices` to draw, keeping the highlight visible.
fn scroll_offset(selected_index: usize, rows: usize) -> usize {
    if selected_index >= rows {
        selected_index - rows + 1
    } else {
        0
    }
}

/// PortAudio indices of the device rows the modal is currently showing. These
/// are exactly the devices the engine should be metering.
fn visible_device_indices(
    state: &MicPickerState,
    devices: &[AudioDeviceInfo],
    rows: usize,
) -> Vec<usize> {
    let rows = rows.max(1);
    let scroll = scroll_offset(state.selected_index, rows);
    state
        .filtered_indices
        .iter()
        .skip(scroll)
        .take(rows)
        .filter_map(|&i| devices.get(i).map(|d| d.index))
        .collect()
}

/// Fixed-width columns for one device row, derived from the modal's inner width
/// so every row ends on the same column.
struct MicRowLayout {
    name_w: usize,
}

impl MicRowLayout {
    const BADGE_W: usize = 10;
    const MARGIN: usize = 1;
    /// pointer(2) + icon(2) + icon gap(1) + name gap(1) + vu + vu gap(1) + pill + margin
    const FIXED: usize = 2 + 2 + 1 + 1 + VU_W + 1 + Self::BADGE_W + Self::MARGIN;

    fn new(total: usize) -> Self {
        // The name is the only flexible column; it absorbs whatever is left so
        // a row never spills past the right border.
        Self {
            name_w: total.saturating_sub(Self::FIXED),
        }
    }

    /// Full display width of a row, margin included.
    #[cfg(test)]
    fn row_width(&self) -> usize {
        Self::FIXED + self.name_w
    }
}

/// Render one audio-device row. Names are clipped/padded by display width so
/// the VU meter and the pill always start on the same columns.
fn mic_row(
    dev: &AudioDeviceInfo,
    is_selected: bool,
    is_active: bool,
    vu_level: f32,
    theme_color: Color,
    layout: &MicRowLayout,
) -> Line<'static> {
    let mut spans: Vec<Span<'static>> = Vec::new();

    // Pointer column.
    if is_selected {
        spans.push(Span::styled(
            "❯ ",
            Style::default().fg(theme_color).add_modifier(Modifier::BOLD),
        ));
    } else {
        spans.push(Span::raw("  "));
    }

    // Icon column (the mic glyph is double width).
    spans.push(Span::styled(format!("{} ", textfit::fit("🎤", 2)), Style::default()));

    // Name column + gap.
    let title = dev.display_name.as_deref().unwrap_or(&dev.name);
    let name_style = if is_selected {
        Style::default().fg(theme_color).add_modifier(Modifier::BOLD)
    } else if is_active {
        Style::default().fg(Color::White).add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(Color::Gray)
    };
    spans.push(Span::styled(
        format!("{} ", textfit::fit(title, layout.name_w)),
        name_style,
    ));

    // Live VU meter for this row's device.
    spans.extend(vu_bar_spans(vu_level, VU_W - 7));
    spans.push(Span::raw(" "));

    // Flatly sized action pill.
    if is_active {
        spans.push(Span::styled(
            textfit::center("[ACTIVE]", MicRowLayout::BADGE_W),
            Style::default()
                .fg(Color::Black)
                .bg(Color::Green)
                .add_modifier(Modifier::BOLD),
        ));
    } else if is_selected {
        spans.push(Span::styled(
            textfit::center("↵ SELECT", MicRowLayout::BADGE_W),
            Style::default()
                .fg(Color::Black)
                .bg(theme_color)
                .add_modifier(Modifier::BOLD),
        ));
    } else {
        spans.push(Span::styled(
            " ".repeat(MicRowLayout::BADGE_W),
            Style::default().fg(Color::DarkGray),
        ));
    }

    Line::from(spans)
}

pub fn render_mic_picker(frame: &mut Frame, state: &MicPickerState, app: &App) {
    let area = frame.area();
    let theme_color = app.effective_color().color();

    // Centered popup modal
    let (popup_w, popup_h) = popup_size(area.width, area.height);
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
        Span::styled("  💡 Live Levels: ", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        Span::styled("every visible device meters at once — speak to see which mic hears you", Style::default().add_modifier(Modifier::DIM)),
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
        let scroll = scroll_offset(selected, max_visible);

        let mut list_lines: Vec<Line<'static>> = Vec::new();
        let layout = MicRowLayout::new(list_area.width as usize);
        for (view_i, &opt_idx) in state
            .filtered_indices
            .iter()
            .skip(scroll)
            .take(max_visible)
            .enumerate()
        {
            let actual_idx = scroll + view_i;
            let is_selected = actual_idx == state.selected_index;
            let dev = &devices[opt_idx];
            let is_active = dev.name.to_lowercase() == app.active_device.to_lowercase()
                || (dev.is_active && app.active_device.is_empty());

            // Every visible row meters at once, so the user can see which device
            // is actually hearing them instead of guessing from the highlight.
            //
            // Identical values across rows are expected on hosts where several
            // device entries resolve to one capture source (WSLg exposes a
            // single PulseAudio source), not a fault in the per-index map.
            let vu_level = app.vu_levels.get(&dev.index).copied().unwrap_or(0.0);

            list_lines.push(mic_row(
                dev,
                is_selected,
                is_active,
                vu_level,
                theme_color,
                &layout,
            ));
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
        Span::styled("Navigate   ", Style::default().add_modifier(Modifier::DIM)),
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

    // Request fresh device list from Python backend. The monitored set is
    // derived from what is actually on screen, so the loop below commands it.
    if let Some(w) = writer {
        ipc::send_cmd(w, "get_devices");
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
        // Do not let an erratic terminal size reading (a resize mid-frame, a
        // terminal that reports 0x0) abort the picker: fall back to a sane
        // default and let the next frame re-measure.
        let (term_w, term_h) = terminal
            .size()
            .map(|s| (s.width, s.height))
            .unwrap_or((80, 24));
        let rows = visible_rows(term_w, term_h);
        let visible = visible_device_indices(&state, &app.audio_devices, rows);

        if writer.is_none() {
            // Demo mode: no engine to meter, so animate the visible rows too.
            app.synthetic_vu();
            let demo: Vec<crate::ipc::VuLevel> = visible
                .iter()
                .map(|i| crate::ipc::VuLevel {
                    i: *i,
                    level: app.vu_level,
                })
                .collect();
            app.update_vu_levels(&demo);
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
                        Wire::Vu { level, levels } => {
                            app.apply_vu_wire(level, &levels);
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

        // Keep the engine metering exactly the rows on screen. This only fires
        // when scrolling or filtering actually changes the visible set.
        if visible != state.monitored {
            state.monitored = visible.clone();
            if let Some(w) = writer {
                ipc::send_cmd_json(w, "start_mic_monitor", serde_json::json!({ "indices": visible }));
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
                                    ipc::send_cmd_json(
                                        w,
                                        "set_device",
                                        serde_json::json!({
                                            "device": dev_name,
                                            "index": dev_index,
                                        }),
                                    );
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
    fn visible_indices_meter_exactly_the_rows_on_screen() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        // Six devices with non-contiguous PortAudio indices, as PipeWire gives us.
        app.audio_devices = [4usize, 5, 6, 8, 9, 12]
            .iter()
            .enumerate()
            .map(|(i, &index)| AudioDeviceInfo {
                index,
                name: format!("Mic {i}"),
                display_name: None,
                channels: 1,
                is_default: i == 0,
                is_active: i == 0,
            })
            .collect();
        let mut state = MicPickerState::new(app.audio_devices.len());

        // Everything fits: every row is metered.
        assert_eq!(
            visible_device_indices(&state, &app.audio_devices, 10),
            vec![4, 5, 6, 8, 9, 12]
        );

        // A short window follows the highlight rather than metering off-screen rows.
        state.selected_index = 4;
        assert_eq!(
            visible_device_indices(&state, &app.audio_devices, 3),
            vec![6, 8, 9]
        );

        // Filtering narrows the metered set to the matching row alone.
        state.query = "mic 3".to_string();
        state.update_filter(&app.audio_devices);
        state.selected_index = 0;
        assert_eq!(visible_device_indices(&state, &app.audio_devices, 10), vec![8]);

        // A filter with no matches monitors nothing (which clears the meters).
        state.query = "zzz".to_string();
        state.update_filter(&app.audio_devices);
        assert!(visible_device_indices(&state, &app.audio_devices, 10).is_empty());
    }

    #[test]
    fn meter_window_matches_the_rendered_list_height() {
        // The renderer and the monitor sync must agree on the row count, or the
        // engine would meter rows the user cannot see.
        for (w, h) in [(80u16, 24u16), (120, 40), (60, 20), (40, 12), (200, 60)] {
            let (popup_w, popup_h) = popup_size(w, h);
            let inner_h = popup_h as usize - 2;
            let expected_rows = inner_h - POPUP_CHROME;
            assert_eq!(visible_rows(w, h), expected_rows.max(1));
            assert!(popup_w >= POPUP_MIN_W && popup_w <= POPUP_MAX_W);
        }
    }

    #[test]
    fn vu_bar_reflects_a_non_highlighted_rows_level() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.audio_devices = vec![AudioDeviceInfo {
            index: 7,
            name: "Quiet USB Mic".to_string(),
            display_name: Some("Quiet USB Mic".to_string()),
            channels: 1,
            is_default: false,
            is_active: false,
        }];

        // Nothing is highlighted or active, yet the row still shows its own level.
        app.apply_vu_wire(0.0, &[crate::ipc::VuLevel { i: 7, level: 0.5 }]);
        assert_eq!(app.vu_levels.get(&7), Some(&0.5));

        // A snapshot that omits the device forgets it (levels are not cumulative).
        app.apply_vu_wire(0.0, &[]);
        assert!(app.vu_levels.is_empty());
    }

    #[test]
    fn mic_picker_filtering() {
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

    #[test]
    fn test_mic_rows_share_one_right_edge() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.audio_devices.push(AudioDeviceInfo {
            index: 9,
            name: "Ünïcødé ÜSB Mic With A Very Long Name".to_string(),
            display_name: Some("Ünïcødé ÜSB Mic With A Very Long Name".to_string()),
            channels: 1,
            is_default: false,
            is_active: false,
        });

        for total in [56usize, 74, 80, 96, 130] {
            let layout = MicRowLayout::new(total);
            assert_eq!(
                layout.row_width(),
                total,
                "mic row overflows a {total}-column list"
            );
            let expected = layout.row_width() - MicRowLayout::MARGIN;
            for (i, dev) in app.audio_devices.iter().enumerate() {
                for is_selected in [true, false] {
                    for is_active in [true, false] {
                        let line = mic_row(
                            dev,
                            is_selected,
                            is_active,
                            app.vu_level,
                            Color::Cyan,
                            &layout,
                        );
                        assert_eq!(
                            line.width(),
                            expected,
                            "device {i} row is ragged at width {total}"
                        );
                    }
                }
            }
        }
    }
}

//! Ratatui frontend for Voice Transcriber.
//!
//! Two modes:
//!   * `--connect <socket>` — IPC client for the Python engine (the real app).
//!   * otherwise            — standalone demo / keyboard-driven prototype.
//!
//! Both render the "Style 1" inline REPL layout: a live status line pinned at the
//! bottom, with transcription/event blocks pushed into terminal scrollback via
//! `Terminal::insert_before`.

mod app;
mod demo;
mod ipc;
mod mic_picker;
mod settings_picker;
mod theme_picker;
mod ui;

use std::io;
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::tty::IsTty;
use ratatui::layout::Rect;
use ratatui::widgets::{Clear, Paragraph, Widget, Wrap};
use ratatui::{DefaultTerminal, TerminalOptions, Viewport};

use app::{Action, App, Level, OutStatus, RunState, Theme};
use demo::Script;
use ipc::{IpcEvent, Wire};

const VERSION: &str = "0.1.0";
const TICK: Duration = Duration::from_millis(100);

fn safe_restore() {
    let _ = std::panic::catch_unwind(|| {
        let _ = crossterm::terminal::disable_raw_mode();
        let _ = crossterm::execute!(io::stdout(), crossterm::cursor::Show);
    });
    let _ = std::panic::catch_unwind(|| {
        ratatui::restore();
    });
}

fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_help();
        return Ok(());
    }

    let demo_enabled = !args.iter().any(|a| a == "--no-demo");
    let theme = args
        .iter()
        .position(|a| a == "--theme")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| Theme::from_name(s))
        .unwrap_or(Theme::Auto);
    let connect = args
        .iter()
        .position(|a| a == "--connect")
        .and_then(|i| args.get(i + 1))
        .cloned();

    if !io::stdout().is_tty() {
        eprintln!("vt-tui: stdout is not a TTY.");
        return Ok(());
    }

    let result = match connect {
        Some(path) => run_ipc(&path, theme),
        None => run_local(demo_enabled, theme),
    };
    safe_restore();
    println!();
    result
}

/// Holds the terminal and the stateful bits of the render loop.
struct Runtime {
    terminal: DefaultTerminal,
    viewport_h: u16,
    suspended: bool,
    header_done: bool,
}

impl Runtime {
    fn new(app: &App) -> io::Result<Self> {
        let (w, _) = crossterm::terminal::size().unwrap_or((80, 24));
        let viewport_h = ui::max_status_height(app, w).max(1);
        let mut terminal = ratatui::init_with_options(TerminalOptions {
            viewport: Viewport::Inline(viewport_h),
        });
        terminal.hide_cursor()?;
        Ok(Self {
            terminal,
            viewport_h,
            suspended: false,
            header_done: false,
        })
    }

    fn width(&self) -> u16 {
        self.terminal.size().map(|s| s.width).unwrap_or(80)
    }

    /// Push the banner into scrollback (once).
    fn emit_header(&mut self, app: &App) -> io::Result<()> {
        if self.header_done || self.suspended {
            return Ok(());
        }
        let width = self.width();
        let header_h = Paragraph::new(ui::header_line(app))
            .wrap(Wrap { trim: false })
            .line_count(width)
            .max(1) as u16;
        self.terminal.insert_before(header_h + 1, |buf| {
            let area = buf.area;
            let header_area = Rect {
                height: area.height.saturating_sub(1),
                ..area
            };
            Paragraph::new(ui::header_line(app))
                .wrap(Wrap { trim: false })
                .render(header_area, buf);
        })?;
        self.header_done = true;
        Ok(())
    }

    fn emit_block(&mut self, block: &ui::Block) -> io::Result<()> {
        if self.suspended {
            return Ok(());
        }
        let width = self.width();
        let height = ui::block_height(block, width);
        self.terminal.insert_before(height, |buf| {
            let area = buf.area;
            let top_area = Rect { height: 1, ..area };
            let bottom_area = Rect {
                x: area.x,
                y: area.y + area.height.saturating_sub(1),
                width: area.width,
                height: 1,
            };
            let body_area = Rect {
                x: area.x,
                y: area.y + 1,
                width: area.width,
                height: area.height.saturating_sub(2),
            };
            Paragraph::new(block.top.clone()).render(top_area, buf);
            Paragraph::new(block.body.clone())
                .wrap(Wrap { trim: false })
                .render(body_area, buf);
            Paragraph::new(block.bottom.clone()).render(bottom_area, buf);
        })
    }

    fn handle_wire(&mut self, app: &mut App, wire: Wire) -> io::Result<()> {
        let width = self.width();
        match wire {
            Wire::Devices { devices } => app.update_devices(devices),
            Wire::State { state, sub } => app.update_state(RunState::from_wire(&state), sub),
            Wire::Vu { level } => app.update_vu(level),
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
            } => app.apply_config(
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
            ),
            Wire::Tx {
                text,
                rec,
                proc,
                ready,
                status,
            } => {
                app.transcription_count += 1;
                let block = ui::transcription_block(
                    app,
                    width,
                    &text,
                    rec,
                    proc,
                    ready,
                    ipc::status_from_wire(status.as_deref()),
                );
                self.emit_block(&block)?;
            }
            Wire::Ev {
                title,
                message,
                level,
            } => {
                let block = ui::event_block(
                    app,
                    width,
                    &title,
                    &message,
                    ipc::level_from_wire(level.as_deref()),
                );
                self.emit_block(&block)?;
            }
            Wire::Header => self.emit_header(app)?,
            Wire::Suspend => self.suspend()?,
            Wire::Resume => self.resume(app)?,
            Wire::Quit => app.should_quit = true,
        }
        Ok(())
    }

    /// Hand the terminal back to Python (used while it runs the settings menu).
    fn suspend(&mut self) -> io::Result<()> {
        if self.suspended {
            return Ok(());
        }
        let _ = self.terminal.show_cursor();
        safe_restore();
        self.suspended = true;
        Ok(())
    }

    fn resume(&mut self, app: &App) -> io::Result<()> {
        if !self.suspended {
            return Ok(());
        }
        let (w, _) = crossterm::terminal::size().unwrap_or((80, 24));
        let h = ui::max_status_height(app, w).max(1);
        self.terminal = ratatui::init_with_options(TerminalOptions {
            viewport: Viewport::Inline(h),
        });
        self.terminal.hide_cursor()?;
        self.viewport_h = h;
        self.suspended = false;
        self.header_done = false;
        self.emit_header(app)?;
        // Swallow any keystrokes still queued for the Python menu (e.g. the 'q'
        // that closed it) so they don't immediately act on the TUI.
        while event::poll(Duration::ZERO)? {
            let _ = event::read()?;
        }
        Ok(())
    }

    /// Re-create the viewport if the terminal resize changed the height we need.
    /// ratatui 0.29 cannot resize an inline viewport in place.
    fn on_resize(&mut self, app: &App) -> io::Result<()> {
        let (w, _) = crossterm::terminal::size().unwrap_or((80, 24));
        let want = ui::max_status_height(app, w).max(1);
        if want != self.viewport_h {
            self.terminal.clear()?;
            self.terminal = ratatui::init_with_options(TerminalOptions {
                viewport: Viewport::Inline(want),
            });
            self.terminal.hide_cursor()?;
            self.viewport_h = want;
        }
        Ok(())
    }

    fn draw(&mut self, app: &App) -> io::Result<()> {
        if self.suspended {
            return Ok(());
        }
        let (w, _) = crossterm::terminal::size().unwrap_or((80, 24));
        let want = ui::max_status_height(app, w).max(1);
        if want != self.viewport_h {
            self.on_resize(app)?;
        }
        self.terminal.draw(|frame| {
            let area = frame.area();
            frame.render_widget(Clear, area);
            let text = ui::status_text(app);
            // Bottom-align within the viewport, and never draw outside it.
            let h = (Paragraph::new(text.clone())
                .wrap(Wrap { trim: false })
                .line_count(area.width) as u16)
                .clamp(1, area.height.max(1));
            let y = area.y + area.height.saturating_sub(h);
            let sub = Rect {
                x: area.x,
                y,
                width: area.width,
                height: h,
            };
            frame.render_widget(Paragraph::new(text).wrap(Wrap { trim: false }), sub);
        })?;
        Ok(())
    }
}

fn run_ipc(path: &str, theme: Theme) -> io::Result<()> {
    let (rx, writer) = ipc::connect(path)?;
    let mut app = App::new(VERSION, theme);
    let mut rt = Runtime::new(&app)?;

    while !app.should_quit {
        app.tick = app.tick.wrapping_add(1);
        app.spinner = app.spinner.wrapping_add(1);
        if app.state != RunState::Recording {
            app.synthetic_vu();
        }

        // Messages from Python.
        let mut incoming = Vec::new();
        while let Ok(ev) = rx.try_recv() {
            incoming.push(ev);
        }
        for ev in incoming {
            match ev {
                IpcEvent::Wire(w) => rt.handle_wire(&mut app, w)?,
                IpcEvent::Closed => app.should_quit = true,
            }
        }

        // Local screen handling.

        if !rt.suspended {
            while event::poll(Duration::ZERO)? {
                match event::read()? {
                    Event::Key(key) if key.kind == KeyEventKind::Press => {
                        if let Some(intent) = key_intent(key) {
                            match intent {
                                Intent::Quit => {
                                    ipc::send_cmd(&writer, "quit");
                                    app.should_quit = true;
                                }
                                Intent::ToggleRecord => ipc::send_cmd(&writer, "toggle_record"),
                                Intent::ChangeDevice => {
                                    mic_picker::run_mic_picker(Some(&writer), Some(&rx), &mut app)?;
                                }
                                Intent::ToggleMute => ipc::send_cmd(&writer, "toggle_mute"),
                                Intent::ToggleAutoType => {
                                    ipc::send_cmd(&writer, "cycle_output_mode")
                                }
                                Intent::ToggleTrailingSpace => {
                                    ipc::send_cmd(&writer, "toggle_trailing_space")
                                }
                                Intent::CyclePunctuation => {
                                    ipc::send_cmd(&writer, "cycle_punctuation")
                                }
                                Intent::ToggleNumbers => ipc::send_cmd(&writer, "toggle_numbers"),
                                Intent::ToggleMiddleClick => {
                                    ipc::send_cmd(&writer, "toggle_middle_click")
                                }
                                Intent::SelectTheme => {
                                    theme_picker::run_theme_picker(app.ui_theme, Some(&writer), Some(&rx), &mut app)?;
                                }
                                Intent::OpenSettingsPicker => {
                                    settings_picker::run_settings_picker(Some(&writer), Some(&rx), &mut app)?;
                                }
                                Intent::CycleTheme => ipc::send_cmd(&writer, "cycle_theme"),
                                Intent::ResetTerminal => ipc::send_cmd(&writer, "reset_terminal"),
                            }
                        }
                    }
                    Event::Resize(_, _) => {
                        let app_snapshot = &app;
                        rt.on_resize(app_snapshot)?;
                    }
                    _ => {}
                }
            }
        }

        rt.draw(&app)?;
        std::thread::sleep(TICK);
    }

    Ok(())
}

fn run_local(demo_enabled: bool, theme: Theme) -> io::Result<()> {
    let mut app = App::new(VERSION, theme);
    let mut script = Script::new();
    if !demo_enabled {
        script.pause();
    }
    let mut rt = Runtime::new(&app)?;
    rt.emit_header(&app)?;
    let mut pending: Option<Pending> = None;
    let mut canned = 0usize;

    while !app.should_quit {
        app.tick = app.tick.wrapping_add(1);
        app.spinner = app.spinner.wrapping_add(1);
        app.synthetic_vu();

        if let Some(p) = &pending {
            if Instant::now() >= p.at {
                let action = pending.take().map(|p| p.action).unwrap();
                apply_local_action(&mut app, &mut rt, action)?;
            }
        }

        for action in script.tick() {
            apply_local_action(&mut app, &mut rt, action)?;
        }

        while event::poll(Duration::ZERO)? {
            match event::read()? {
                Event::Key(key) if key.kind == KeyEventKind::Press => {
                    if let Some(intent) = key_intent(key) {
                        match intent {
                            Intent::Quit => app.should_quit = true,
                            Intent::ToggleRecord => {
                                if app.state == RunState::Ready {
                                    script.pause();
                                    app.update_state(RunState::Recording, String::new());
                                } else if app.state == RunState::Recording {
                                    app.update_state(
                                        RunState::Processing,
                                        format!(
                                            "Transcribing stream with {}...",
                                            titlecase(&app.model_backend)
                                        ),
                                    );
                                    canned = canned.wrapping_add(1);
                                    let text = match canned % 3 {
                                        0 => "Hold on, let me think about the ratatui layout for a second.",
                                        1 => "The inline viewport keeps the status line pinned while results scroll.",
                                        _ => "This prototype mirrors the Rich TUI without the Python engine.",
                                    };
                                    pending = Some(Pending {
                                        at: Instant::now() + Duration::from_millis(1100),
                                        action: Action::Transcribe {
                                            text: text.to_string(),
                                            rec: 1.8,
                                            proc: 1.1,
                                            ready: 0.42,
                                            status: OutStatus::Typed,
                                        },
                                    });
                                }
                            }
                            Intent::ChangeDevice => {
                                mic_picker::run_mic_picker(None, None, &mut app)?;
                            }
                            Intent::ToggleMute => app.is_muted = !app.is_muted,
                            Intent::ToggleAutoType => {
                                app.output_mode = match app.output_mode.as_str() {
                                    "clipboard" => "type".to_string(),
                                    "type" => "type_fast".to_string(),
                                    _ => "clipboard".to_string(),
                                };
                                app.auto_type = app.output_mode == "type" || app.output_mode == "type_fast";
                            }
                            Intent::ToggleTrailingSpace => {
                                app.trailing_space = !app.trailing_space;
                            }
                            Intent::CyclePunctuation => {
                                app.cycle_punctuation();
                            }
                            Intent::ToggleNumbers => {}
                            Intent::ToggleMiddleClick => {}
                            Intent::SelectTheme => {
                                theme_picker::run_theme_picker(app.ui_theme, None, None, &mut app)?;
                            }
                            Intent::OpenSettingsPicker => {
                                settings_picker::run_settings_picker(None, None, &mut app)?;
                            }
                            Intent::CycleTheme => {
                                app.cycle_theme();
                            }
                            Intent::ResetTerminal => {}
                        }
                    }
                }
                Event::Resize(_, _) => {
                    let app_snapshot = &app;
                    rt.on_resize(app_snapshot)?;
                }
                _ => {}
            }
        }

        rt.draw(&app)?;
        std::thread::sleep(TICK);
    }

    let block = ui::event_block(
        &app,
        rt.width(),
        "SESSION ENDED",
        "vt-tui exiting. Terminal restored.",
        Level::Info,
    );
    rt.emit_block(&block)?;
    rt.terminal.show_cursor()?;
    Ok(())
}

fn apply_local_action(app: &mut App, rt: &mut Runtime, action: Action) -> io::Result<()> {
    match action {
        Action::Transcribe {
            text,
            rec,
            proc,
            ready,
            status,
        } => {
            app.transcription_count += 1;
            let width = rt.width();
            let block = ui::transcription_block(app, width, &text, rec, proc, ready, status);
            rt.emit_block(&block)?;
            app.update_state(RunState::Ready, String::new());
        }
        Action::Event {
            title,
            message,
            level,
        } => {
            let width = rt.width();
            let block = ui::event_block(app, width, &title, &message, level);
            rt.emit_block(&block)?;
        }
        other => app.apply(other),
    }
    Ok(())
}

struct Pending {
    at: Instant,
    action: Action,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Intent {
    ToggleRecord,
    ChangeDevice,
    ToggleMute,
    ToggleAutoType,
    ToggleTrailingSpace,
    ToggleNumbers,
    ToggleMiddleClick,
    CyclePunctuation,
    #[allow(dead_code)]
    CycleTheme,
    SelectTheme,
    OpenSettingsPicker,
    ResetTerminal,
    Quit,
}

fn key_intent(key: KeyEvent) -> Option<Intent> {
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        return Some(Intent::Quit);
    }
    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => Some(Intent::Quit),
        KeyCode::Char(' ') | KeyCode::Enter => Some(Intent::ToggleRecord),
        KeyCode::Char('m') => Some(Intent::ToggleMute),
        KeyCode::Char('c') => Some(Intent::ToggleAutoType),
        KeyCode::Char('s') => Some(Intent::ToggleTrailingSpace),
        KeyCode::Char('S') | KeyCode::Char(',') => Some(Intent::OpenSettingsPicker),
        KeyCode::Char('p') | KeyCode::Char('P') => Some(Intent::CyclePunctuation),
        KeyCode::Char('n') => Some(Intent::ToggleNumbers),
        KeyCode::Char('o') | KeyCode::Char('O') => Some(Intent::ToggleMiddleClick),
        KeyCode::Char('t') | KeyCode::Char('T') => Some(Intent::SelectTheme),
        KeyCode::Char('r') => Some(Intent::ResetTerminal),
        KeyCode::Char('M') | KeyCode::Char('i') | KeyCode::Char('I') => Some(Intent::ChangeDevice),
        _ => None,
    }
}

fn titlecase(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(first) => first.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

fn print_help() {
    println!(
        "vt-tui {VERSION} — ratatui frontend for Voice Transcriber\n\
         \n\
         USAGE:\n    vt-tui [--connect <socket>] [--no-demo] [--theme <name>]\n\
         \n\
         OPTIONS:\n\
         \x20   --connect <path>  Connect to the Python engine over a Unix socket\n\
         \x20   --no-demo         Standalone mode: start idle, drive from the keyboard\n\
         \x20   --theme <name>    auto | green | cyan | blue | magenta | yellow | red | white\n\
         \x20   -h, --help        Print this help\n\
         \n\
         KEYS:\n\
         \x20   Space / Enter     Start / stop recording\n\
         \x20   M, i              Audio device / settings menu\n\
         \x20   m                 Toggle sound effects\n\
         \x20   c                 Cycle output mode (Clipboard / Type / Paste / Paste Terminal)\n\
         \x20   n                 Toggle number-to-digits\n\
         \x20   o                 Toggle middle click push-to-talk\n\
         \x20   t                 Cycle UI theme\n\
         \x20   r                 Reset terminal & clipboard bridge\n\
         \x20   q, Esc, Ctrl+C    Quit"
    );
}

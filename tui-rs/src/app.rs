//! Application state for the ratatui prototype.
//!
//! This deliberately mirrors the state held by `src/tui.py` (`VoiceTranscriberTUI`)
//! so the two implementations can be compared side by side.

use std::time::Instant;

use ratatui::style::Color;

/// UI colour palettes. Mirrors `COLOR_PALETTES` in `src/tui.py`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Theme {
    Auto,
    Green,
    Cyan,
    Blue,
    Magenta,
    Yellow,
    Red,
    White,
}

impl Theme {
    pub const ALL: [Theme; 8] = [
        Theme::Auto,
        Theme::Green,
        Theme::Cyan,
        Theme::Blue,
        Theme::Magenta,
        Theme::Yellow,
        Theme::Red,
        Theme::White,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Theme::Auto => "auto",
            Theme::Green => "green",
            Theme::Cyan => "cyan",
            Theme::Blue => "blue",
            Theme::Magenta => "magenta",
            Theme::Yellow => "yellow",
            Theme::Red => "red",
            Theme::White => "white",
        }
    }

    pub fn from_name(s: &str) -> Option<Theme> {
        Theme::ALL
            .iter()
            .copied()
            .find(|t| t.name() == s.trim().to_lowercase())
    }

    pub fn next(self) -> Theme {
        let i = Theme::ALL.iter().position(|t| *t == self).unwrap_or(0);
        Theme::ALL[(i + 1) % Theme::ALL.len()]
    }

    pub fn color(self) -> Color {
        match self {
            Theme::Auto | Theme::Green => Color::Green,
            Theme::Cyan => Color::Cyan,
            Theme::Blue => Color::Blue,
            Theme::Magenta => Color::Magenta,
            Theme::Yellow => Color::Yellow,
            Theme::Red => Color::Red,
            Theme::White => Color::White,
        }
    }
}

/// State machine matching the Python `self.state` values.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum RunState {
    Ready,
    Recording,
    Processing,
    Rewriting,
    Config(String),
}

impl RunState {
    /// Map the Python backend's state string onto our state machine.
    pub fn from_wire(s: &str) -> RunState {
        match s.to_uppercase().as_str() {
            "READY" => RunState::Ready,
            "RECORDING" => RunState::Recording,
            "PROCESSING" => RunState::Processing,
            "REWRITING" => RunState::Rewriting,
            other => RunState::Config(other.to_string()),
        }
    }
}

#[allow(dead_code)] // mirrors the Python OutStatus set; not all states are demoed
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum OutStatus {
    Typed,
    Copied,
    CopyError,
}

#[allow(dead_code)] // mirrors the Python event levels
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Level {
    Info,
    Success,
    Warning,
    Error,
}

impl Level {
    pub fn color(self) -> Color {
        match self {
            Level::Info => Color::Green,
            Level::Success => Color::Green,
            Level::Warning => Color::Yellow,
            Level::Error => Color::Red,
        }
    }
}

/// Actions produced by the demo script (or by the keyboard in interactive mode).
#[derive(Clone)]
pub enum Action {
    State(RunState, String),
    Transcribe {
        text: String,
        rec: f32,
        proc: f32,
        ready: f32,
        status: OutStatus,
    },
    Event {
        title: String,
        message: String,
        level: Level,
    },
    SetTheme(Theme),
    SetMute(bool),
}

pub struct App {
    pub version: String,
    pub state: RunState,
    pub sub_state_text: String,
    pub state_started: Option<Instant>,
    pub rec_started: Option<Instant>,
    pub vu_level: f32,
    pub vu_peak: f32,
    pub spinner: usize,
    pub transcription_count: usize,
    pub active_device: String,
    #[allow(dead_code)]
    pub secondary_device: Option<String>,
    pub model_backend: String,
    pub is_muted: bool,
    pub auto_type: bool,
    pub output_mode: String,
    #[allow(dead_code)]
    pub sound_theme: String,
    pub ui_theme: Theme,
    pub should_quit: bool,
    pub tick: u64,
}

impl App {
    pub fn new(version: &str, ui_theme: Theme) -> Self {
        Self {
            version: version.to_string(),
            state: RunState::Ready,
            sub_state_text: String::new(),
            state_started: None,
            rec_started: None,
            vu_level: 0.0,
            vu_peak: 0.0,
            spinner: 0,
            transcription_count: 0,
            active_device: "HyperX QuadCast S".to_string(),
            secondary_device: Some("Built-in Analog Stereo".to_string()),
            model_backend: "cohere".to_string(),
            is_muted: true,
            auto_type: false,
            output_mode: "clipboard".to_string(),
            sound_theme: "proximity".to_string(),
            ui_theme,
            should_quit: false,
            tick: 0,
        }
    }

    /// Resolve the effective colour, mirroring `detect_system_theme_color()`.
    pub fn effective_color(&self) -> Theme {
        if self.ui_theme != Theme::Auto {
            return self.ui_theme;
        }
        detect_system_theme()
    }

    pub fn elapsed_secs(&self) -> f32 {
        self.state_started
            .map(|t| t.elapsed().as_secs_f32())
            .unwrap_or(0.0)
    }

    pub fn rec_elapsed_secs(&self) -> f32 {
        self.rec_started
            .map(|t| t.elapsed().as_secs_f32())
            .unwrap_or(0.0)
    }

    /// `update_state()` from the Python TUI.
    pub fn update_state(&mut self, state: RunState, sub_text: String) {
        let starts_timer = matches!(
            state,
            RunState::Recording | RunState::Processing | RunState::Rewriting
        );
        if starts_timer && self.state_started.is_none() {
            self.state_started = Some(Instant::now());
        }
        if state == RunState::Ready {
            self.state_started = None;
            self.vu_level = 0.0;
            self.vu_peak = 0.0;
            self.rec_started = None;
        }
        if state == RunState::Recording {
            self.rec_started = Some(Instant::now());
        }
        self.state = state;
        self.sub_state_text = sub_text;
    }

    /// Synthesise a plausible mic level so the VU meter animates in the demo.
    pub fn update_vu(&mut self, level: f32) {
        self.vu_level = level.max(self.vu_level * 0.7);
        self.vu_peak = self.vu_peak.max(self.vu_level);
    }

    pub fn synthetic_vu(&mut self) {
        if self.state != RunState::Recording {
            self.vu_level *= 0.8;
            return;
        }
        let t = self.rec_elapsed_secs();
        let base = 0.35 + 0.25 * ((t * 2.3).sin() * 0.5 + 0.5);
        let wobble = 0.20 * ((t * 7.7).sin() * 0.5 + 0.5) + 0.15 * ((t * 13.1).sin() * 0.5 + 0.5);
        // Occasional "pause" so it doesn't look like a pure sine wave.
        let gate = if (t * 1.7).sin() > -0.35 { 1.0 } else { 0.25 };
        self.update_vu(((base + wobble) * gate).min(1.0));
    }

    /// Apply a state-mutating action. Transcription/event blocks are handled by the
    /// caller because they need terminal access (`insert_before`).
    pub fn apply(&mut self, action: Action) {
        match action {
            Action::State(s, sub) => self.update_state(s, sub),
            Action::SetTheme(t) => self.ui_theme = t,
            Action::SetMute(m) => self.is_muted = m,
            Action::Transcribe { .. } | Action::Event { .. } => {
                // handled by caller
            }
        }
    }

    pub fn cycle_theme(&mut self) -> Theme {
        self.ui_theme = self.ui_theme.next();
        self.ui_theme
    }

    /// Apply a `cfg` message from the Python backend (authoritative config).
    pub fn apply_config(
        &mut self,
        mic: Option<String>,
        secondary: Option<String>,
        backend: Option<String>,
        muted: Option<bool>,
        auto_type: Option<bool>,
        output_mode: Option<String>,
        sound_theme: Option<String>,
        ui_theme: Option<String>,
    ) {
        if let Some(m) = mic {
            self.active_device = m;
        }
        if let Some(s) = secondary {
            self.secondary_device = Some(s);
        }
        if let Some(b) = backend {
            self.model_backend = b;
        }
        if let Some(m) = muted {
            self.is_muted = m;
        }
        if let Some(mode) = output_mode {
            self.output_mode = mode;
            self.auto_type = self.output_mode == "type" || self.output_mode == "type_fast";
        } else if let Some(a) = auto_type {
            self.auto_type = a;
            self.output_mode = if a { "type".to_string() } else { "clipboard".to_string() };
        }
        if let Some(s) = sound_theme {
            self.sound_theme = s;
        }
        if let Some(t) = ui_theme {
            if let Some(theme) = Theme::from_name(&t) {
                self.ui_theme = theme;
            }
        }
    }
}

fn detect_system_theme() -> Theme {
    if let Ok(t) = std::env::var("VT_UI_THEME") {
        if let Some(theme) = Theme::from_name(&t) {
            if theme != Theme::Auto {
                return theme;
            }
        }
    }
    if let Ok(accent) = std::env::var("ACCENT_COLOR") {
        if let Some(theme) = Theme::from_name(&accent) {
            return theme;
        }
    }
    if let Ok(prog) = std::env::var("TERM_PROGRAM") {
        let prog = prog.to_lowercase();
        if prog.contains("vscode") {
            return Theme::Cyan;
        } else if prog.contains("kitty") {
            return Theme::Magenta;
        } else if prog.contains("apple_terminal") {
            return Theme::Blue;
        } else if prog.contains("alacritty") {
            return Theme::Yellow;
        }
    }
    Theme::Green
}

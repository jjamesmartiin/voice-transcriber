//! Application state for the ratatui prototype.
//!
//! This deliberately mirrors the state held by `src/tui.py` (`VoiceTranscriberTUI`)
//! so the two implementations can be compared side by side.

use std::collections::HashMap;
use std::time::Instant;

use ratatui::style::Color;

use crate::ipc::Wire;

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
    /// Resolve the event colour. Info, Success, and Warning follow the user's
    /// chosen theme colour; critical Errors remain Red.
    pub fn color(self, theme_color: Color) -> Color {
        match self {
            Level::Error => Color::Red,
            _ => theme_color,
        }
    }

    #[allow(dead_code)]
    pub fn default_color(self) -> Color {
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
    /// Timestamp of the last scalar VU update, used to decay the meter in real
    /// time rather than once per message.
    vu_last_update: Instant,
    /// Per-device live levels driving the mic picker's rows, keyed by PortAudio
    /// device index. Only populated while the picker is monitoring.
    pub vu_levels: HashMap<usize, f32>,
    /// Timestamp of the last per-device VU snapshot (see `update_vu_levels`).
    vu_levels_last_update: Instant,
    pub spinner: usize,
    pub transcription_count: usize,
    pub active_device: String,
    pub audio_devices: Vec<crate::ipc::AudioDeviceInfo>,
    #[allow(dead_code)]
    pub secondary_device: Option<String>,
    pub model_backend: String,
    pub is_muted: bool,
    pub auto_type: bool,
    pub output_mode: String,
    pub trailing_space: bool,
    pub auto_punctuate: bool,
    pub number_digits: bool,
    pub number_mode: String,
    pub serial_collapse: bool,
    pub spell_command: bool,
    pub typing_wpm: u32,
    /// Canonical push-to-talk chord spellings from the engine, e.g.
    /// `["alt+shift", "f13"]`. Empty until the first `cfg` payload arrives.
    pub hotkeys: Vec<String>,
    pub middle_click_enabled: bool,
    pub punctuation_mode: String,
    /// Configured list-formatting mode ("off"/"inline"/"blocks").
    pub structure_mode: String,
    /// Configured formatter toggle ("off"/"on").
    pub formatter: String,
    /// Configured formatter backend identifier (e.g. "s1-mini").
    pub formatter_model: String,
    /// Configured formatter writing style ("casual"/"semi-casual"/"semi-formal"/"formal").
    pub formatter_style: String,
    /// Configured formatter context ("general"/"email").
    pub formatter_context: String,
    /// Configured cleanup mode ("off"/"artifacts"/"full"): how much of the
    /// post-processing pass may change the words. See docs/cleanup_modes.md.
    pub cleanup_mode: String,
    #[allow(dead_code)]
    pub sound_theme: String,
    pub ui_theme: Theme,
    pub should_quit: bool,
    pub tick: u64,
    /// Transcription/event blocks received while a modal owned the screen.
    /// The modal runs on the alternate screen, where `Terminal::insert_before`
    /// cannot render them; they are queued here and flushed once the modal
    /// returns to the main screen. Mirrors Python's `TUI._pending` queue.
    pub pending_output: Vec<Wire>,
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
            vu_last_update: Instant::now(),
            vu_levels: HashMap::new(),
            vu_levels_last_update: Instant::now(),
            spinner: 0,
            transcription_count: 0,
            active_device: "HyperX QuadCast S".to_string(),
            audio_devices: vec![
                crate::ipc::AudioDeviceInfo {
                    index: 0,
                    name: "HyperX QuadCast S".to_string(),
                    display_name: Some("HyperX QuadCast S".to_string()),
                    channels: 2,
                    is_default: true,
                    is_active: true,
                },
                crate::ipc::AudioDeviceInfo {
                    index: 1,
                    name: "Built-in Analog Stereo".to_string(),
                    display_name: Some("Built-in Analog Stereo".to_string()),
                    channels: 2,
                    is_default: false,
                    is_active: false,
                },
            ],
            secondary_device: Some("Built-in Analog Stereo".to_string()),
            model_backend: "cohere".to_string(),
            is_muted: true,
            auto_type: false,
            output_mode: "clipboard".to_string(),
            trailing_space: true,
            auto_punctuate: true,
            number_digits: true,
            number_mode: "auto".to_string(),
            serial_collapse: true,
            spell_command: true,
            typing_wpm: 40,
            hotkeys: Vec::new(),
            middle_click_enabled: false,
            punctuation_mode: "full".to_string(),
            structure_mode: "off".to_string(),
            formatter: "off".to_string(),
            formatter_model: "s1-mini".to_string(),
            formatter_style: "semi-formal".to_string(),
            formatter_context: "general".to_string(),
            cleanup_mode: "full".to_string(),
            sound_theme: "proximity".to_string(),
            ui_theme,
            should_quit: false,
            tick: 0,
            pending_output: Vec::new(),
        }
    }

    /// Resolve the effective colour, mirroring `detect_system_theme_color()`.
    pub fn effective_color(&self) -> Theme {
        if self.ui_theme != Theme::Auto {
            return self.ui_theme;
        }
        detect_system_theme()
    }

    /// Replace the device list. `None` means the producer omitted the field
    /// (no update); `Some([])` is a legitimate "no microphones" result and must
    /// clear the stale rows.
    pub fn update_devices(&mut self, devs: Option<Vec<crate::ipc::AudioDeviceInfo>>) {
        if let Some(devs) = devs {
            self.audio_devices = devs;
        }
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
            self.vu_levels.clear();
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
        let decay = self.vu_decay();
        self.vu_level = level.max(self.vu_level * decay);
        self.vu_peak = self.vu_peak.max(self.vu_level);
    }

    /// Exponential decay over the time since the previous scalar update. The
    /// old implementation multiplied by a fixed `0.7` per message, so the meter
    /// fell faster the more frames arrived; tying it to elapsed time keeps the
    /// visual fall-off independent of message cadence.
    fn vu_decay(&mut self) -> f32 {
        let now = Instant::now();
        let dt = now
            .saturating_duration_since(self.vu_last_update)
            .as_secs_f32();
        self.vu_last_update = now;
        decay_factor(dt)
    }

    /// Apply one `vu` wire message: the scalar feeds the main meter and the
    /// optional per-device list feeds the mic picker's rows. A scalar-only
    /// message (`None`) leaves the per-device map untouched.
    pub fn apply_vu_wire(&mut self, level: f32, levels: Option<&[crate::ipc::VuLevel]>) {
        self.update_vu(level);
        if let Some(levels) = levels {
            self.update_vu_levels(levels);
        }
    }

    /// Replace the per-device level map with a fresh snapshot, decaying any
    /// device that got quieter and forgetting devices that stopped reporting.
    /// An empty snapshot therefore clears every row.
    pub fn update_vu_levels(&mut self, levels: &[crate::ipc::VuLevel]) {
        let now = Instant::now();
        let dt = now
            .saturating_duration_since(self.vu_levels_last_update)
            .as_secs_f32();
        self.vu_levels_last_update = now;
        let decay = decay_factor(dt);
        let mut next: HashMap<usize, f32> = HashMap::with_capacity(levels.len());
        for l in levels {
            let decayed = self.vu_levels.get(&l.i).copied().unwrap_or(0.0) * decay;
            next.insert(l.i, l.level.max(decayed));
        }
        self.vu_levels = next;
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

    #[allow(dead_code)] // theme selection now lives in the settings modal
    pub fn cycle_theme(&mut self) -> Theme {
        self.ui_theme = self.ui_theme.next();
        self.ui_theme
    }

    /// Advance the list-formatting mode through the three the engine knows
    /// (``t2.STRUCTURE_MODES == ["off", "inline", "blocks"]``). The engine is
    /// authoritative and replies with a `cfg` that overwrites this optimistic
    /// value, so we must never invent a mode it cannot round-trip.
    pub fn cycle_structure_mode(&mut self) -> &str {
        const STRUCTURE_MODES: [&str; 3] = ["off", "inline", "blocks"];
        let idx = STRUCTURE_MODES
            .iter()
            .position(|m| *m == self.structure_mode)
            .map(|i| (i + 1) % STRUCTURE_MODES.len())
            .unwrap_or(0);
        self.structure_mode = STRUCTURE_MODES[idx].to_string();
        &self.structure_mode
    }

    /// Advance the formatter toggle between the two states the engine knows
    /// (``formatter.BACKENDS`` gates the rewrite; ``off`` disables it). The
    /// engine is authoritative and replies with a `cfg` that overwrites this
    /// optimistic value.
    pub fn cycle_formatter(&mut self) -> &str {
        const FORMATTER_MODES: [&str; 2] = ["off", "on"];
        let idx = FORMATTER_MODES
            .iter()
            .position(|m| *m == self.formatter)
            .map(|i| (i + 1) % FORMATTER_MODES.len())
            .unwrap_or(0);
        self.formatter = FORMATTER_MODES[idx].to_string();
        &self.formatter
    }

    /// Advance the formatter backend through the two shipped names. The engine
    /// is authoritative and replies with a `cfg` that overwrites this value.
    pub fn cycle_formatter_model(&mut self) -> &str {
        const FORMATTER_MODELS: [&str; 2] = ["s1-mini", "llama-server"];
        let idx = FORMATTER_MODELS
            .iter()
            .position(|m| *m == self.formatter_model)
            .map(|i| (i + 1) % FORMATTER_MODELS.len())
            .unwrap_or(0);
        self.formatter_model = FORMATTER_MODELS[idx].to_string();
        &self.formatter_model
    }

    /// Advance the formatter writing style through the four the engine knows.
    /// The engine is authoritative and replies with a `cfg` that overwrites
    /// this optimistic value.
    pub fn cycle_formatter_style(&mut self) -> &str {
        const FORMATTER_STYLES: [&str; 4] = ["casual", "semi-casual", "semi-formal", "formal"];
        let idx = FORMATTER_STYLES
            .iter()
            .position(|m| *m == self.formatter_style)
            .map(|i| (i + 1) % FORMATTER_STYLES.len())
            .unwrap_or(0);
        self.formatter_style = FORMATTER_STYLES[idx].to_string();
        &self.formatter_style
    }

    /// Advance the formatter context between the two the engine knows. The
    /// engine is authoritative and replies with a `cfg` that overwrites this
    /// optimistic value.
    pub fn cycle_formatter_context(&mut self) -> &str {
        const FORMATTER_CONTEXTS: [&str; 2] = ["general", "email"];
        let idx = FORMATTER_CONTEXTS
            .iter()
            .position(|m| *m == self.formatter_context)
            .map(|i| (i + 1) % FORMATTER_CONTEXTS.len())
            .unwrap_or(0);
        self.formatter_context = FORMATTER_CONTEXTS[idx].to_string();
        &self.formatter_context
    }

    /// Advance the cleanup mode through the three the engine knows
    /// (``t2.CLEANUP_MODES == ["off", "artifacts", "full"]``). As with the other
    /// cycles, the engine is authoritative and replies with a `cfg` that
    /// overwrites this optimistic value.
    pub fn cycle_cleanup_mode(&mut self) -> &str {
        const CLEANUP_MODES: [&str; 3] = ["off", "artifacts", "full"];
        let idx = CLEANUP_MODES
            .iter()
            .position(|m| *m == self.cleanup_mode)
            .map(|i| (i + 1) % CLEANUP_MODES.len())
            .unwrap_or(0);
        self.cleanup_mode = CLEANUP_MODES[idx].to_string();
        &self.cleanup_mode
    }

    #[allow(dead_code)] // retained to mirror Python's `cycle_punctuation`; the
                        // preset modal owns punctuation selection on this frontend.
    pub fn cycle_punctuation(&mut self) {
        self.punctuation_mode = match self.punctuation_mode.as_str() {
            "full" | "default" => "no_terminal_period".to_string(),
            "no_terminal_period" | "casual" => "no_punctuation".to_string(),
            "no_punctuation" | "autocorrect" => "aesthetic_lowercase".to_string(),
            "aesthetic_lowercase" | "aesthetic" => "lowercase_no_punctuation".to_string(),
            _ => "full".to_string(),
        };
    }

    /// Advance the output mode through the three modes the engine actually
    /// knows (``t2.OUTPUT_MODES == ["clipboard", "type", "type_fast"]``,
    /// ``src/voice_transcriber/t2.py:122``). The engine is authoritative and
    /// replies with a `cfg` that overwrites this optimistic value, so we must
    /// never invent a mode it cannot round-trip.
    pub fn cycle_output_mode(&mut self) -> &str {
        const OUTPUT_MODES: [&str; 3] = ["clipboard", "type", "type_fast"];
        let idx = OUTPUT_MODES
            .iter()
            .position(|m| *m == self.output_mode)
            .map(|i| (i + 1) % OUTPUT_MODES.len())
            .unwrap_or(0);
        self.output_mode = OUTPUT_MODES[idx].to_string();
        self.auto_type = matches!(self.output_mode.as_str(), "type" | "type_fast");
        &self.output_mode
    }

    /// Apply a wire message received while a modal owns the screen.
    ///
    /// State, VU, device and config updates are applied immediately so the
    /// modal stays live. Transcription (`tx`) and event (`ev`) blocks need the
    /// main screen's scrollback, which the modal's alternate screen cannot
    /// write to; dropping them would lose output the engine already considers
    /// delivered. Queue them instead and let the caller flush them once the
    /// modal returns (`take_pending_output`).
    pub fn handle_modal_wire(&mut self, wire: Wire) {
        match wire {
            Wire::Tx { .. } | Wire::Ev { .. } => self.pending_output.push(wire),
            Wire::Devices { devices } => self.update_devices(devices),
            Wire::State { state, sub } => self.update_state(RunState::from_wire(&state), sub),
            Wire::Vu { level, levels } => self.apply_vu_wire(level, levels.as_deref()),
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
                structure_mode,
                formatter,
                formatter_model,
                formatter_style,
                formatter_context,
                cleanup_mode,
                trailing_space,
                auto_punctuate,
                number_digits,
                number_mode,
                serial_collapse,
                spell_command,
                middle_click_enabled,
                typing_wpm,
                hotkeys,
            } => self.apply_config(
                mic,
                secondary,
                backend,
                muted,
                auto_type,
                output_mode,
                sound_theme,
                ui_theme,
                punctuation_mode,
                structure_mode,
                formatter,
                formatter_model,
                formatter_style,
                formatter_context,
                cleanup_mode,
                trailing_space,
                auto_punctuate,
                number_digits,
                number_mode,
                serial_collapse,
                spell_command,
                middle_click_enabled,
                typing_wpm,
                hotkeys,
            ),
            Wire::Quit => self.should_quit = true,
            Wire::Header | Wire::Suspend | Wire::Resume => {}
        }
    }

    /// Return and clear the blocks buffered while a modal was open, so the main
    /// loop can render them into the scrollback.
    pub fn take_pending_output(&mut self) -> Vec<Wire> {
        std::mem::take(&mut self.pending_output)
    }

    pub fn cycle_typing_wpm(&mut self) -> u32 {
        const PRESETS: [u32; 7] = [30, 40, 50, 60, 70, 80, 100];
        let next = match PRESETS.iter().position(|&x| x == self.typing_wpm) {
            Some(idx) => PRESETS[(idx + 1) % PRESETS.len()],
            None => PRESETS
                .iter()
                .copied()
                .find(|&x| x > self.typing_wpm)
                .unwrap_or(PRESETS[0]),
        };
        self.typing_wpm = next;
        next
    }

    /// Apply a `cfg` message from the Python backend (authoritative config).
    #[allow(clippy::too_many_arguments)] // protocol-shaped: mirrors the wire fields 1:1
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
        punctuation_mode: Option<String>,
        structure_mode: Option<String>,
        formatter: Option<String>,
        formatter_model: Option<String>,
        formatter_style: Option<String>,
        formatter_context: Option<String>,
        cleanup_mode: Option<String>,
        trailing_space: Option<bool>,
        auto_punctuate: Option<bool>,
        number_digits: Option<bool>,
        number_mode: Option<String>,
        serial_collapse: Option<bool>,
        spell_command: Option<bool>,
        middle_click_enabled: Option<bool>,
        typing_wpm: Option<u32>,
        hotkeys: Option<Vec<String>>,
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
            self.output_mode = if a {
                "type".to_string()
            } else {
                "clipboard".to_string()
            };
        }
        if let Some(s) = sound_theme {
            self.sound_theme = s;
        }
        if let Some(t) = ui_theme {
            if let Some(theme) = Theme::from_name(&t) {
                self.ui_theme = theme;
            }
        }
        if let Some(p) = punctuation_mode {
            self.punctuation_mode = p;
        }
        if let Some(s) = structure_mode {
            self.structure_mode = s;
        }
        if let Some(f) = formatter {
            self.formatter = f;
        }
        if let Some(m) = formatter_model {
            self.formatter_model = m;
        }
        if let Some(s) = formatter_style {
            self.formatter_style = s;
        }
        if let Some(c) = formatter_context {
            self.formatter_context = c;
        }
        if let Some(c) = cleanup_mode {
            self.cleanup_mode = c;
        }
        if let Some(sp) = trailing_space {
            self.trailing_space = sp;
        }
        if let Some(ap) = auto_punctuate {
            self.auto_punctuate = ap;
        }
        if let Some(m) = number_mode {
            self.number_mode = m;
            self.number_digits = self.number_mode != "words";
        } else if let Some(n) = number_digits {
            self.number_digits = n;
            self.number_mode = (if n { "digits" } else { "words" }).to_string();
        }
        if let Some(mc) = middle_click_enabled {
            self.middle_click_enabled = mc;
        }
        if let Some(sc) = serial_collapse {
            self.serial_collapse = sc;
        }
        if let Some(s) = spell_command {
            self.spell_command = s;
        }
        if let Some(w) = typing_wpm {
            self.typing_wpm = w;
        }
        if let Some(h) = hotkeys {
            self.hotkeys = h;
        }
    }
}

/// Per-message decay the meter used before it became time-based: the level was
/// multiplied by `0.7` on every update, at a nominal 100 ms message cadence.
/// `decay_factor` preserves that visual half-rate while scaling it by the real
/// elapsed time, so a burst of frames cannot drain the bar and a stall cannot
/// freeze it.
fn decay_factor(dt_secs: f32) -> f32 {
    const NOMINAL_DT_SECS: f32 = 0.1;
    const PER_STEP: f32 = 0.7;
    PER_STEP.powf(dt_secs / NOMINAL_DT_SECS)
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ipc::VuLevel;

    /// The engine only round-trips ``t2.OUTPUT_MODES``; the Rust cycle must
    /// never drift outside it (it used to invent `paste`/`paste_terminal`).
    #[test]
    fn output_mode_cycle_stays_within_the_engine_modes() {
        const ENGINE_MODES: [&str; 3] = ["clipboard", "type", "type_fast"];
        let mut app = App::new("1.1.1", Theme::Cyan);
        for start in ENGINE_MODES {
            app.output_mode = start.to_string();
            for _ in 0..(ENGINE_MODES.len() * 3) {
                app.cycle_output_mode();
                assert!(
                    ENGINE_MODES.contains(&app.output_mode.as_str()),
                    "cycle produced {:?}",
                    app.output_mode
                );
                assert_eq!(
                    app.auto_type,
                    matches!(app.output_mode.as_str(), "type" | "type_fast")
                );
            }
        }
    }

    /// Decay is a function of elapsed time, not of how many frames arrived.
    #[test]
    fn vu_decay_is_time_based() {
        // One nominal step reproduces the historical 0.7 per message...
        assert!((decay_factor(0.1) - 0.7).abs() < 1e-6);
        // ...while a longer gap decays strictly further than a short one.
        assert!(decay_factor(0.5) < decay_factor(0.05));
        assert!(decay_factor(2.0) < decay_factor(0.5));
    }

    /// A scalar-only VU message carries no per-device snapshot and must leave
    /// the mic picker's rows alone; an explicit empty list clears them.
    #[test]
    fn scalar_vu_leaves_the_per_device_map_alone() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        app.update_vu_levels(&[VuLevel { i: 7, level: 0.5 }]);
        app.apply_vu_wire(0.0, None);
        assert_eq!(app.vu_levels.get(&7), Some(&0.5));
        app.apply_vu_wire(0.0, Some(&[]));
        assert!(app.vu_levels.is_empty());
    }

    /// `tx` received while a modal is open is queued, not dropped, and the
    /// counter is not advanced until it is actually flushed.
    #[test]
    fn modal_buffers_transcription_until_flushed() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        let wire: Wire = serde_json::from_str(r#"{"t":"tx","text":"hello"}"#).unwrap();
        app.handle_modal_wire(wire);
        assert_eq!(app.transcription_count, 0, "not applied yet");
        assert_eq!(app.pending_output.len(), 1);

        let pending = app.take_pending_output();
        assert_eq!(pending.len(), 1);
        assert!(matches!(pending[0], Wire::Tx { .. }));
        assert!(app.pending_output.is_empty(), "take clears the queue");
    }

    /// An empty device list is a real result (no microphones) and must clear
    /// stale rows; an omitted list means "no update".
    #[test]
    fn empty_device_list_clears_rows_but_absent_does_not() {
        let mut app = App::new("1.1.1", Theme::Cyan);
        assert!(!app.audio_devices.is_empty());
        app.update_devices(None);
        assert!(
            !app.audio_devices.is_empty(),
            "absent list is not an update"
        );
        app.update_devices(Some(Vec::new()));
        assert!(app.audio_devices.is_empty(), "empty list clears the rows");
    }
}

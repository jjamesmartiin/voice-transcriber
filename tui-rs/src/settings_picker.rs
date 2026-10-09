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
use crate::ipc::{self, IpcEvent};
use crate::textfit;
use crate::theme_picker;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SettingKind {
    TrailingSpace,
    #[allow(dead_code)]
    AutoPunctuate,
    NumberDigits,
    SerialCollapse,
    SpellCommand,
    TypingWpm,
    MiddleClick,
    Hotkeys,
    SoundMute,
    OutputMode,
    PunctuationMode,
    StructureMode,
    Formatter,
    FormatterModel,
    FormatterStyle,
    FormatterContext,
    CleanupMode,
    MeetingMode,
    MeetingSpill,
    Theme,
    Microphone,
    RescanMics,
    ResetTerminal,
    ResetDefaults,
}

#[derive(Clone, Copy, Debug)]
pub struct SettingItem {
    pub kind: SettingKind,
    pub icon: &'static str,
    pub title: &'static str,
    pub keywords: &'static str,
}

pub const SETTINGS: [SettingItem; 23] = [
    // NOTE: icons must be exactly one glyph whose *own* codepoint already
    // occupies its final width in every terminal - never a `U+FE0F`
    // variation-selector sequence and never a ZWJ sequence. Terminals that
    // ignore the emoji-presentation rule render e.g. `✍️` (U+270D is
    // East-Asian-Neutral) as a single narrow cell while ratatui budgets two,
    // which shifts that whole row - including its pill and right border - one
    // column left. `test_setting_icons_have_terminal_independent_width` pins
    // this. A narrow glyph such as `␣` is fine: `RowLayout` pads the icon
    // column to a fixed two cells.
    SettingItem {
        kind: SettingKind::PunctuationMode,
        icon: "✨",
        title: "Mode Preset",
        keywords: "mode preset switcher formatting gen z casual autocorrect aesthetic default punctuation capitalization grammar",
    },
    SettingItem {
        kind: SettingKind::StructureMode,
        icon: "•",
        title: "List Formatting",
        keywords: "structure list bullet bullets enumeration spoken lists formatting newline paragraph break clipboard inline",
    },
    SettingItem {
        kind: SettingKind::Formatter,
        icon: "•",
        title: "Formatter",
        keywords: "formatter format rewrite rewrite transcript local slm llm cleanup deterministic off on",
    },
    SettingItem {
        kind: SettingKind::FormatterModel,
        icon: "•",
        title: "Formatter Model",
        keywords: "formatter model backend slm llm local s1-mini llama-server rewrite",
    },
    SettingItem {
        kind: SettingKind::FormatterStyle,
        icon: "•",
        title: "Formatter Style",
        keywords: "formatter style writing casual semi-casual semi-formal formal tone rewrite",
    },
    SettingItem {
        kind: SettingKind::FormatterContext,
        icon: "•",
        title: "Formatter Context",
        keywords: "formatter context general email message writing rewrite",
    },
    SettingItem {
        kind: SettingKind::CleanupMode,
        icon: "•",
        title: "Cleanup Mode",
        keywords: "cleanup mode corrections retractions hallucination filler stutter verbatim artifacts noise full off",
    },
    SettingItem {
        kind: SettingKind::MeetingMode,
        icon: "•",
        title: "Meeting Mode",
        keywords: "meeting diarization speakers capture long recording minutes hours transcript notes",
    },
    SettingItem {
        kind: SettingKind::MeetingSpill,
        icon: "•",
        title: "Meeting Spill",
        keywords: "meeting spill memory temp file threshold minutes long recording buffer disk",
    },
    SettingItem {
        kind: SettingKind::OutputMode,
        icon: "🚀",
        title: "Output Delivery",
        keywords: "output mode delivery clipboard type typing paste speed fast slow safe",
    },
    SettingItem {
        kind: SettingKind::TrailingSpace,
        icon: "␣",
        title: "Trailing Space",
        keywords: "trailing space auto type whitespace append space",
    },
    SettingItem {
        kind: SettingKind::NumberDigits,
        icon: "🔢",
        title: "Number Conversion",
        keywords: "numbers digits words spelled format numeric conversion",
    },
    SettingItem {
        kind: SettingKind::SerialCollapse,
        icon: "🔤",
        title: "Serial/Codes",
        keywords: "serial code alphanumeric nato phonetic spelled collapse spacing identifier model vin license plate",
    },
    SettingItem {
        kind: SettingKind::SpellCommand,
        icon: "🔠",
        title: "Spell Command",
        keywords: "spell spelled verbal command letters c a t acronym dictation",
    },
    SettingItem {
        kind: SettingKind::TypingWpm,
        icon: "⚡",
        title: "Typing Speed",
        keywords: "typing speed wpm words per minute time saved benchmark calculation stats",
    },
    SettingItem {
        kind: SettingKind::MiddleClick,
        icon: "👆",
        title: "Mouse Hotkey",
        keywords: "middle click mouse hotkey push to talk button hold",
    },
    SettingItem {
        kind: SettingKind::Hotkeys,
        icon: "🔑",
        title: "Push-to-Talk Keys",
        keywords: "push to talk hotkey key bind chord shortcut alt shift ptt keyboard f13 right ctrl add remove",
    },
    SettingItem {
        kind: SettingKind::SoundMute,
        icon: "🔔",
        title: "Sound Effects",
        keywords: "sound effects mute volume chimes audio audio cues notify",
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
        kind: SettingKind::RescanMics,
        icon: "♻",
        title: "Reset Microphones",
        keywords: "reset rescan refresh microphones mics devices audio input list enumerate plugged busy stale missing",
    },
    SettingItem {
        kind: SettingKind::ResetTerminal,
        icon: "🔄",
        title: "Reset Terminal",
        keywords: "reset terminal clipboard bridge clear state fix",
    },
    SettingItem {
        kind: SettingKind::ResetDefaults,
        icon: "🔙",
        title: "Reset to Defaults",
        keywords: "reset defaults factory restore revert shipped initial config settings default everything",
    },
];

impl SettingItem {
    pub fn value_and_badge(
        &self,
        app: &App,
        state: &SettingsPickerState,
    ) -> (String, &'static str, Color) {
        let c = app.effective_color().color();
        match self.kind {
            SettingKind::TrailingSpace => {
                if app.trailing_space {
                    (
                        "Appends a space after typing".to_string(),
                        "[ON]",
                        Color::Green,
                    )
                } else {
                    (
                        "No space added after typing".to_string(),
                        "[OFF]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::AutoPunctuate => {
                if app.auto_punctuate {
                    (
                        "Full punctuation + period".to_string(),
                        "[ON]",
                        Color::Green,
                    )
                } else {
                    (
                        "Respect the mode preset".to_string(),
                        "[OFF]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::NumberDigits => match app.number_mode.as_str() {
                "digits" => (
                    "Every number becomes digits".to_string(),
                    "[DIGITS]",
                    Color::Green,
                ),
                "words" => (
                    "Numbers stay as words".to_string(),
                    "[WORDS]",
                    Color::DarkGray,
                ),
                _ => (
                    "Runs of 2+ become digits".to_string(),
                    "[AUTO]",
                    Color::Cyan,
                ),
            },
            SettingKind::SerialCollapse => {
                if app.serial_collapse {
                    (
                        "Keeps ABC123 as one token".to_string(),
                        "[COLLAPSE]",
                        Color::Cyan,
                    )
                } else {
                    (
                        "Each letter typed separately".to_string(),
                        "[SPACED]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::SpellCommand => {
                if app.spell_command {
                    (
                        "Say 'spell C A T' for CAT".to_string(),
                        "[ON]",
                        Color::Green,
                    )
                } else {
                    (
                        "Spell command ignored".to_string(),
                        "[OFF]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::TypingWpm => {
                let badge = match app.typing_wpm {
                    20 => "[20 WPM]",
                    30 => "[30 WPM]",
                    40 => "[40 WPM]",
                    50 => "[50 WPM]",
                    60 => "[60 WPM]",
                    70 => "[70 WPM]",
                    80 => "[80 WPM]",
                    90 => "[90 WPM]",
                    100 => "[100 WPM]",
                    120 => "[120 WPM]",
                    _ => "[WPM]",
                };
                (
                    format!("{} WPM · time saved baseline", app.typing_wpm),
                    badge,
                    Color::Cyan,
                )
            }
            SettingKind::MiddleClick => {
                if app.middle_click_enabled {
                    (
                        "Hold middle click · L+R = Enter".to_string(),
                        "[ON]",
                        Color::Green,
                    )
                } else {
                    (
                        "Middle click acts normally".to_string(),
                        "[OFF]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::Hotkeys => {
                let desc = match app.hotkeys.len() {
                    0 => "No keys bound · press Enter".to_string(),
                    1 => "1 key bound · press Enter".to_string(),
                    n => format!("{n} keys bound · press Enter"),
                };
                (desc, "[EDIT]", Color::Yellow)
            }
            SettingKind::SoundMute => {
                if !app.is_muted {
                    ("Chimes on record & stop".to_string(), "[ON]", Color::Green)
                } else {
                    ("No sound cues play".to_string(), "[MUTED]", Color::Red)
                }
            }
            SettingKind::OutputMode => {
                let (desc, badge) = match app.output_mode.as_str() {
                    "type_fast" => ("Auto-type, optimized", "[FAST]"),
                    "type" => ("Auto-type, safe speed", "[SLOW]"),
                    // The engine only ever reports the three modes in
                    // `t2.OUTPUT_MODES`; clipboard is the fallback.
                    _ => ("Copies to clipboard only", "[CLIP]"),
                };
                (desc.to_string(), badge, Color::Cyan)
            }
            SettingKind::PunctuationMode => {
                // Previews come from docs/mode_presets.md: every preset is shown
                // against the same sample sentence so the styles are directly
                // comparable. `test_preset_previews_match_the_spec` and
                // tests/shared/test_mode_presets.py keep this in step with
                // post_processor.apply_punctuation_mode().
                let (desc, badge, color) = match app.punctuation_mode.as_str() {
                    "no_terminal_period" | "casual" => {
                        ("\"Hey, how are you? I'm good\"", "[CASUAL]", Color::Yellow)
                    }
                    "no_punctuation" | "autocorrect" => {
                        ("\"Hey how are you I'm good\"", "[PHONE]", Color::Blue)
                    }
                    "aesthetic_lowercase" | "aesthetic" => {
                        ("\"hey, how are you? i'm good\"", "[AESTH]", Color::Magenta)
                    }
                    "lowercase_no_punctuation" | "gen_z" => {
                        ("\"hey how are you i'm good\"", "[GEN Z]", Color::Cyan)
                    }
                    _ => ("\"Hey, how are you? I'm good.\"", "[DEFAULT]", Color::Green),
                };
                (desc.to_string(), badge, color)
            }
            SettingKind::CleanupMode => {
                // Spec: docs/cleanup_modes.md. "off" keeps every word (nothing is
                // deleted), "artifacts" deletes noise only, "full" also resolves
                // what the speaker retracted.
                let (desc, badge, color) = match app.cleanup_mode.as_str() {
                    "off" => ("Keeps every word", "[OFF]", Color::DarkGray),
                    "artifacts" => ("Removes noise only", "[NOISE]", Color::Cyan),
                    _ => ("Resolves corrections", "[FULL]", Color::Green),
                };
                (desc.to_string(), badge, color)
            }
            SettingKind::MeetingMode => {
                // Spec: docs/meeting_mode.md. "off" is the shipped default, so
                // the dictation path is byte-identical to a build without the
                // feature and `meeting-start` refuses to run.
                if app.meeting_mode == "on" {
                    (
                        "Long, non-injecting capture".to_string(),
                        "[ON]",
                        Color::Green,
                    )
                } else {
                    ("Off: dictation only".to_string(), "[OFF]", Color::DarkGray)
                }
            }
            SettingKind::MeetingSpill => (
                format!("Spills to disk past {} min", app.meeting_spill_minutes),
                "[SPILL]",
                Color::Cyan,
            ),
            SettingKind::StructureMode => {
                // Spec: docs/formatting.md. "blocks" emits real line breaks, and
                // a newline is an Enter keypress, so the engine downgrades it to
                // "inline" while the output is typed. The badge says so rather
                // than advertising a formatting that is not in force.
                let (desc, badge, color) = match app.structure_mode.as_str() {
                    "inline" => (
                        "Bullets on one line: - one. - two.",
                        "[INLINE]",
                        Color::Cyan,
                    ),
                    "blocks" => {
                        if matches!(app.output_mode.as_str(), "type" | "type_fast") {
                            ("Typed text stays inline", "[PASTE]", Color::Yellow)
                        } else {
                            ("Bullets on their own lines", "[BLOCKS]", Color::Green)
                        }
                    }
                    _ => ("Flat prose (no list formatting)", "[OFF]", Color::DarkGray),
                };
                (desc.to_string(), badge, color)
            }
            // Formatter labels (spec): "Off: deterministic cleanup only",
            // "Rewrites the transcript on this machine", "Model: {value}",
            // "Writing style: {value}", "Context: {value}".
            SettingKind::Formatter => {
                // Spec: docs/plan-on-device-formatter.md. The formatter is the
                // only component in the codebase that may invent text, so "off"
                // is the shipped default: deterministic cleanup only.
                if app.formatter == "on" {
                    (
                        "Rewrites the transcript on this machine".to_string(),
                        "[ON]",
                        Color::Green,
                    )
                } else {
                    (
                        "Off: deterministic cleanup only".to_string(),
                        "[OFF]",
                        Color::DarkGray,
                    )
                }
            }
            SettingKind::FormatterModel => (
                format!("Model: {}", app.formatter_model),
                "[MODEL]",
                Color::Cyan,
            ),
            SettingKind::FormatterStyle => (
                format!("Writing style: {}", app.formatter_style),
                "[STYLE]",
                Color::Cyan,
            ),
            SettingKind::FormatterContext => (
                format!("Context: {}", app.formatter_context),
                "[CONTEXT]",
                Color::Cyan,
            ),
            SettingKind::Theme => {
                let name = app.ui_theme.name();
                (format!("{} palette", name), "[PICKER]", c)
            }
            SettingKind::Microphone => {
                // The device name is user data and can be arbitrarily long, so
                // it is the one value handed to the row renderer untruncated:
                // `textfit` only clips it if it truly cannot fit.
                (app.active_device.clone(), "[SELECT]", Color::Yellow)
            }
            SettingKind::RescanMics => {
                if state.mic_rescan_done {
                    (
                        "Audio devices re-scanned".to_string(),
                        "[DONE]",
                        Color::Green,
                    )
                } else {
                    (
                        "Re-scan for microphones".to_string(),
                        "[RUN]",
                        Color::Yellow,
                    )
                }
            }
            SettingKind::ResetTerminal => {
                if state.reset_done {
                    (
                        "Terminal & clipboard reset".to_string(),
                        "[DONE]",
                        Color::Green,
                    )
                } else {
                    (
                        "Reset terminal & clipboard state".to_string(),
                        "[RUN]",
                        Color::Yellow,
                    )
                }
            }
            SettingKind::ResetDefaults => {
                if state.defaults_done {
                    ("All settings restored".to_string(), "[DONE]", Color::Green)
                } else if state.confirm_defaults {
                    (
                        "Press Enter again to confirm".to_string(),
                        "[SURE?]",
                        Color::Red,
                    )
                } else {
                    (
                        "Restore all shipped defaults".to_string(),
                        "[RESET]",
                        Color::Red,
                    )
                }
            }
        }
    }
}

/// Fixed-width column layout shared by every settings row. Deriving the
/// description column from the modal's inner width (instead of a magic
/// constant) is what keeps the right edge — and therefore every status pill —
/// on the same column for every row.
struct RowLayout {
    icon_w: usize,
    title_w: usize,
    desc_w: usize,
    badge_w: usize,
}

impl RowLayout {
    const TITLE_W: usize = 19;
    const ICON_W: usize = 2;
    const BADGE_W: usize = 10;
    /// Minimum cells reserved for the description column.
    const MIN_DESC_W: usize = 8;
    /// The title column is never squeezed below this.
    const MIN_TITLE_W: usize = 8;
    /// Blank columns kept between the pill and the right border.
    const MARGIN: usize = 1;
    /// pointer(2) + icon + icon gap(1) + description gap(1) + pill + margin
    const FIXED: usize = 2 + Self::ICON_W + 1 + 1 + Self::BADGE_W + Self::MARGIN;

    fn new(total: usize) -> Self {
        // Cells left over for the two flexible columns.
        let flexible = total.saturating_sub(Self::FIXED);
        // Prefer the comfortable title width, but hand columns back to the
        // description on narrow terminals: title_w + desc_w always equals
        // `flexible`, so a row never spills past the right border.
        let title_w = Self::TITLE_W
            .min(flexible.saturating_sub(Self::MIN_DESC_W))
            .max(Self::MIN_TITLE_W.min(flexible));
        Self {
            icon_w: Self::ICON_W,
            title_w,
            desc_w: flexible.saturating_sub(title_w),
            badge_w: Self::BADGE_W,
        }
    }

    /// Total display width of a rendered row, right margin included.
    #[cfg(test)]
    fn row_width(&self) -> usize {
        Self::FIXED + self.title_w + self.desc_w
    }
}

/// Render one settings row. Every column is padded by display width so the
/// status pill always lands on the same columns, whatever the icon or the
/// description contains.
fn setting_row(
    item: &SettingItem,
    val_str: &str,
    badge: &str,
    badge_color: Color,
    is_selected: bool,
    theme_color: Color,
    layout: &RowLayout,
) -> Line<'static> {
    let mut spans: Vec<Span<'static>> = Vec::new();

    // Cursor / pointer column.
    if is_selected {
        spans.push(Span::styled(
            "❯ ",
            Style::default()
                .fg(theme_color)
                .add_modifier(Modifier::BOLD),
        ));
    } else {
        spans.push(Span::raw("  "));
    }

    // Icon column, padded to a fixed display width (emoji are double width).
    spans.push(Span::styled(
        format!("{} ", textfit::fit(item.icon, layout.icon_w)),
        Style::default(),
    ));

    // Title column.
    let title_style = if is_selected {
        Style::default()
            .fg(theme_color)
            .add_modifier(Modifier::BOLD)
    } else {
        Style::default()
            .fg(Color::White)
            .add_modifier(Modifier::BOLD)
    };
    spans.push(Span::styled(
        textfit::fit(item.title, layout.title_w),
        title_style,
    ));

    // Description column (leading gap + padded text).
    spans.push(Span::styled(
        format!(" {}", textfit::fit(val_str, layout.desc_w)),
        Style::default().add_modifier(Modifier::DIM),
    ));

    // Status pill, always exactly `badge_w` cells wide.
    let badge_str = textfit::center(badge, layout.badge_w);
    if is_selected {
        spans.push(Span::styled(
            badge_str,
            Style::default()
                .fg(Color::Black)
                .bg(badge_color)
                .add_modifier(Modifier::BOLD),
        ));
    } else {
        spans.push(Span::styled(badge_str, Style::default().fg(badge_color)));
    }

    Line::from(spans)
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

#[derive(Clone, PartialEq, Eq, Debug)]
pub enum SettingsPickerAction {
    Toggle(SettingKind),
    SetWpm(u32),
    Close,
    Continue,
}

pub struct SettingsPickerState {
    pub query: String,
    pub selected_index: usize,
    pub filtered_indices: Vec<usize>,
    pub reset_done: bool,
    /// True once "Reset Microphones" ran, until the picker is closed.
    pub mic_rescan_done: bool,
    /// True once "Reset to Defaults" ran, until the picker is closed.
    pub defaults_done: bool,
    /// Arming flag: the factory reset needs a second Enter/Space to commit.
    pub confirm_defaults: bool,
    /// Active WPM input buffer when prompt dialog is open.
    pub wpm_input: Option<String>,
}

impl SettingsPickerState {
    pub fn new() -> Self {
        Self {
            query: String::new(),
            selected_index: 0,
            filtered_indices: (0..SETTINGS.len()).collect(),
            reset_done: false,
            mic_rescan_done: false,
            defaults_done: false,
            confirm_defaults: false,
            wpm_input: None,
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

    /// Gate for the destructive "Reset to Defaults" action. The first call
    /// only arms it and returns `false`; a second consecutive call (Enter or
    /// Space again, with no other keystroke in between) commits it.
    pub fn confirm_reset_defaults(&mut self) -> bool {
        if self.confirm_defaults {
            self.confirm_defaults = false;
            self.defaults_done = true;
            true
        } else {
            self.confirm_defaults = true;
            false
        }
    }

    pub fn handle_key(&mut self, key: KeyEvent) -> SettingsPickerAction {
        if let Some(ref mut input) = self.wpm_input {
            match key.code {
                KeyCode::Esc => {
                    self.wpm_input = None;
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Enter => {
                    let parsed = if input.is_empty() {
                        None
                    } else {
                        input.trim().parse::<u32>().ok()
                    };
                    self.wpm_input = None;
                    if let Some(val) = parsed {
                        if val > 0 && val <= 500 {
                            return SettingsPickerAction::SetWpm(val);
                        }
                    }
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Backspace => {
                    input.pop();
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    input.clear();
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    self.wpm_input = None;
                    return SettingsPickerAction::Continue;
                }
                KeyCode::Char(c) if c.is_ascii_digit() => {
                    if input.len() < 3 {
                        input.push(c);
                    }
                    return SettingsPickerAction::Continue;
                }
                _ => return SettingsPickerAction::Continue,
            }
        }

        // Arming the destructive factory reset survives only until the next
        // keystroke that is not the confirming Enter/Space.
        let is_activate =
            key.code == KeyCode::Enter || (key.code == KeyCode::Char(' ') && self.query.is_empty());
        if !is_activate {
            self.confirm_defaults = false;
        }

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
                    let kind = SETTINGS[idx].kind;
                    if kind == SettingKind::TypingWpm {
                        self.wpm_input = Some(String::new());
                        SettingsPickerAction::Continue
                    } else {
                        SettingsPickerAction::Toggle(kind)
                    }
                } else {
                    SettingsPickerAction::Close
                }
            }
            KeyCode::Char(' ') => {
                // If search query is empty, Space toggles the selected setting!
                // If user is actively typing a multi-word search, space appends to query.
                if self.query.is_empty() {
                    if let Some(&idx) = self.filtered_indices.get(self.selected_index) {
                        let kind = SETTINGS[idx].kind;
                        if kind == SettingKind::TypingWpm {
                            self.wpm_input = Some(String::new());
                            return SettingsPickerAction::Continue;
                        } else {
                            return SettingsPickerAction::Toggle(kind);
                        }
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

    // Centered popup modal. Both dimensions are clamped to the screen: a
    // minimum larger than the terminal used to size the popup past it, and
    // every layout chunk inherited that oversized rect.
    let popup_w = 72u16
        .min(area.width.saturating_sub(4))
        .max(34)
        .min(area.width);
    let popup_h = 18u16
        .min(area.height.saturating_sub(2))
        .max(12)
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
        .border_style(
            Style::default()
                .fg(theme_color)
                .add_modifier(Modifier::BOLD),
        )
        .title(Span::styled(
            " ⚙️  Settings & Configuration ",
            Style::default()
                .fg(theme_color)
                .add_modifier(Modifier::BOLD),
        ));

    // Paragraph never clips to the frame buffer, so the inner rect has to be
    // intersected here or the settings list writes past the last row (a panic
    // on terminals shorter than the popup minimum).
    let inner = block.inner(popup_area.intersection(area));
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
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
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
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
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
            Span::styled(
                "  No settings match '",
                Style::default().fg(Color::DarkGray),
            ),
            Span::styled(&state.query, Style::default().fg(Color::Yellow)),
            Span::styled(
                "'. Press Backspace or Esc.",
                Style::default().fg(Color::DarkGray),
            ),
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
        let layout = RowLayout::new(list_area.width as usize);
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
            let (val_str, badge, badge_color) = item.value_and_badge(app, state);

            list_lines.push(setting_row(
                item,
                &val_str,
                badge,
                badge_color,
                is_selected,
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
    frame.render_widget(Paragraph::new(Line::from(vec![bot_sep])), chunks[3]);

    // Footer
    let footer = Line::from(vec![
        Span::styled(" [↑/↓] ", Style::default().fg(Color::Cyan)),
        Span::styled("Navigate   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Enter/Space] ", Style::default().fg(Color::Green)),
        Span::styled(
            "Toggle/Action   ",
            Style::default().add_modifier(Modifier::DIM),
        ),
        Span::styled("[Type] ", Style::default().fg(Color::Cyan)),
        Span::styled("Filter   ", Style::default().add_modifier(Modifier::DIM)),
        Span::styled("[Esc] ", Style::default().fg(Color::Red)),
        Span::styled("Done", Style::default().add_modifier(Modifier::DIM)),
    ]);
    frame.render_widget(Paragraph::new(footer), chunks[4]);

    if let Some(ref input) = state.wpm_input {
        let prompt_w = 52u16.min(area.width.saturating_sub(4)).max(34);
        let prompt_h = 7u16.min(area.height.saturating_sub(2)).max(5);
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
            .border_style(
                Style::default()
                    .fg(theme_color)
                    .add_modifier(Modifier::BOLD),
            )
            .title(Span::styled(
                " ⚡ Typing Speed (WPM) ",
                Style::default()
                    .fg(theme_color)
                    .add_modifier(Modifier::BOLD),
            ));

        let p_inner = p_block.inner(prompt_area);
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
            " Enter typing speed in words/minute:",
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
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
                    Style::default()
                        .fg(theme_color)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    format!("{}", app.typing_wpm),
                    Style::default().add_modifier(Modifier::DIM),
                ),
                Span::styled("█", Style::default().fg(theme_color)),
                Span::styled(" words/min (current)", Style::default().fg(Color::DarkGray)),
            ]
        } else {
            vec![
                Span::styled(
                    " ❯ ",
                    Style::default()
                        .fg(theme_color)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    input.clone(),
                    Style::default()
                        .fg(Color::Yellow)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled("█", Style::default().fg(theme_color)),
                Span::styled(" words/min", Style::default().fg(Color::Cyan)),
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
            Span::styled("Save   ", Style::default().add_modifier(Modifier::DIM)),
            Span::styled("[Esc] ", Style::default().fg(Color::Red)),
            Span::styled("Cancel", Style::default().add_modifier(Modifier::DIM)),
        ]);
        frame.render_widget(Paragraph::new(footer), p_chunks[4]);
    }
}

/// Hand the screen over to a nested modal picker (theme / microphone).
///
/// The nested pickers own the alternate screen while they are up, so we step
/// out of ours, let them run, then re-enter a *fresh* alternate screen. The
/// `clear()` is essential: the screen we come back to is blank, while our
/// `Terminal` still holds the last frame it drew - without it the next
/// `draw()` diffs to nothing and the modal stays invisible (blank screen).
fn hand_over_screen<T>(
    terminal: &mut ratatui::Terminal<ratatui::backend::CrosstermBackend<io::Stdout>>,
    body: impl FnOnce() -> io::Result<T>,
) -> io::Result<T> {
    crossterm::execute!(io::stdout(), crossterm::terminal::LeaveAlternateScreen)?;
    let result = body();
    crossterm::execute!(io::stdout(), crossterm::terminal::EnterAlternateScreen)?;
    crossterm::terminal::enable_raw_mode()?;
    terminal.clear()?;
    terminal.hide_cursor()?;
    result
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
        // Drain incoming messages. State/VU/config updates apply live;
        // transcription and event blocks are queued by `handle_modal_wire`
        // because the alternate screen cannot render into the scrollback, and
        // are flushed by the main loop once this modal returns.
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
        // The inner `break` only leaves the drain loop; the engine is gone, so
        // leave the modal loop too instead of hanging on a dead socket.
        if app.should_quit {
            break;
        }

        terminal.draw(|f| render_settings_picker(f, &state, app))?;

        if crossterm::event::poll(Duration::from_millis(40))? {
            if let Event::Key(key) = crossterm::event::read()? {
                if key.kind == KeyEventKind::Press {
                    match state.handle_key(key) {
                        SettingsPickerAction::Close => break,
                        SettingsPickerAction::Continue => {}
                        SettingsPickerAction::SetWpm(val) => {
                            app.typing_wpm = val;
                            if let Some(w) = writer {
                                if ipc::send_cmd_value(w, "set_typing_wpm", "wpm", &val.to_string())
                                    .is_err()
                                {
                                    app.should_quit = true;
                                }
                            }
                        }
                        SettingsPickerAction::Toggle(kind) => {
                            match kind {
                                SettingKind::TrailingSpace => {
                                    app.trailing_space = !app.trailing_space;
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_trailing_space").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::AutoPunctuate => {
                                    app.auto_punctuate = !app.auto_punctuate;
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_auto_punctuate").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::NumberDigits => {
                                    app.number_mode = match app.number_mode.as_str() {
                                        "auto" => "digits".to_string(),
                                        "digits" => "words".to_string(),
                                        _ => "auto".to_string(),
                                    };
                                    app.number_digits = app.number_mode != "words";
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_numbers").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::SerialCollapse => {
                                    app.serial_collapse = !app.serial_collapse;
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_serial_collapse").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::SpellCommand => {
                                    app.spell_command = !app.spell_command;
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_spell_command").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::TypingWpm => {
                                    app.cycle_typing_wpm();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_typing_wpm").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::MiddleClick => {
                                    app.middle_click_enabled = !app.middle_click_enabled;
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_middle_click").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::Hotkeys => {
                                    // Nested push-to-talk bind picker modal.
                                    let _ = hand_over_screen(&mut terminal, || {
                                        crate::bind_picker::run_bind_picker(writer, rx, app)
                                    });
                                }
                                SettingKind::SoundMute => {
                                    app.is_muted = !app.is_muted;
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "toggle_mute").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::CleanupMode => {
                                    app.cycle_cleanup_mode();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_cleanup").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::MeetingMode => {
                                    app.cycle_meeting_mode();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_meeting").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::MeetingSpill => {
                                    app.cycle_meeting_spill();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_meeting_spill").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::StructureMode => {
                                    app.cycle_structure_mode();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_structure").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::Formatter => {
                                    app.cycle_formatter();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_formatter").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::FormatterModel => {
                                    app.cycle_formatter_model();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_formatter_model").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::FormatterStyle => {
                                    app.cycle_formatter_style();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_formatter_style").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::FormatterContext => {
                                    app.cycle_formatter_context();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_formatter_context").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::OutputMode => {
                                    app.cycle_output_mode();
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "cycle_output_mode").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                }
                                SettingKind::PunctuationMode => {
                                    // Nested preset picker modal.
                                    let _ = hand_over_screen(&mut terminal, || {
                                        crate::preset_picker::run_preset_picker(writer, rx, app)
                                    });
                                }
                                SettingKind::Theme => {
                                    // Nested theme picker modal.
                                    let _ = hand_over_screen(&mut terminal, || {
                                        theme_picker::run_theme_picker(
                                            app.ui_theme,
                                            writer,
                                            rx,
                                            app,
                                        )
                                    });
                                }
                                SettingKind::Microphone => {
                                    // Nested microphone picker modal.
                                    let _ = hand_over_screen(&mut terminal, || {
                                        crate::mic_picker::run_mic_picker(writer, rx, app)
                                    });
                                }
                                SettingKind::RescanMics => {
                                    // The engine re-enumerates PortAudio and
                                    // reports what came back (including any mic
                                    // another app is still holding) as an event.
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "rescan_mics").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                    state.mic_rescan_done = true;
                                }
                                SettingKind::ResetTerminal => {
                                    if let Some(w) = writer {
                                        if ipc::send_cmd(w, "reset_terminal").is_err() {
                                            app.should_quit = true;
                                        }
                                    }
                                    state.reset_done = true;
                                    let _ = crossterm::terminal::enable_raw_mode();
                                    let _ =
                                        crossterm::execute!(io::stdout(), crossterm::cursor::Hide);
                                    let _ = terminal.clear();
                                }
                                SettingKind::ResetDefaults => {
                                    if state.confirm_reset_defaults() {
                                        if let Some(w) = writer {
                                            if ipc::send_cmd(w, "reset_defaults").is_err() {
                                                app.should_quit = true;
                                            }
                                        }
                                        let _ = crossterm::terminal::enable_raw_mode();
                                        let _ = crossterm::execute!(
                                            io::stdout(),
                                            crossterm::cursor::Hide
                                        );
                                        let _ = terminal.clear();
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
        let app = App::new("1.1.1", Theme::Cyan);
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
        let (val, badge, _) = SETTINGS[0].value_and_badge(&app, &state);
        assert!(!val.is_empty());
        assert!(!badge.is_empty());

        let state2 = SettingsPickerState::new();
        let backend = ratatui::backend::TestBackend::new(80, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal
            .draw(|f| render_settings_picker(f, &state2, &app))
            .unwrap();
    }

    /// A terminal shorter than the popup's minimum used to panic: the centered
    /// popup was force-sized past the screen, every chunk inherited that
    /// oversized rect, and `Paragraph` (unlike `Block`) does not clip to the
    /// frame buffer, so the settings list wrote row 3 of a 3-row buffer.
    #[test]
    fn test_render_on_a_terminal_shorter_than_the_popup_minimum() {
        let app = App::new("1.1.1", Theme::Cyan);
        for (w, h) in [(174, 3), (80, 5), (20, 2), (1, 1)] {
            let state = SettingsPickerState::new();
            let backend = ratatui::backend::TestBackend::new(w, h);
            let mut terminal = ratatui::Terminal::new(backend).unwrap();
            terminal
                .draw(|f| render_settings_picker(f, &state, &app))
                .unwrap_or_else(|e| panic!("{w}x{h} failed to draw: {e}"));
        }
    }

    #[test]
    fn test_typing_wpm_prompt_and_input() {
        let app = App::new("1.1.1", Theme::Cyan);
        let mut state = SettingsPickerState::new();

        // Search for 'speed'
        state.query = "speed".to_string();
        state.update_filter();
        let top_match = SETTINGS[state.filtered_indices[0]].kind;
        assert_eq!(top_match, SettingKind::TypingWpm);

        // Press Enter to open WPM prompt
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(action, SettingsPickerAction::Continue);
        assert!(state.wpm_input.is_some());

        // Render with prompt active
        let backend = ratatui::backend::TestBackend::new(80, 24);
        let mut terminal = ratatui::Terminal::new(backend).unwrap();
        terminal
            .draw(|f| render_settings_picker(f, &state, &app))
            .unwrap();

        // Type '6', '5'
        state.handle_key(KeyEvent::new(KeyCode::Char('6'), KeyModifiers::NONE));
        state.handle_key(KeyEvent::new(KeyCode::Char('5'), KeyModifiers::NONE));
        assert_eq!(state.wpm_input.as_deref(), Some("65"));

        // Press Enter to submit
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(action, SettingsPickerAction::SetWpm(65));
        assert!(state.wpm_input.is_none());

        // Cancel test: open again, type, hit Esc
        state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert!(state.wpm_input.is_some());
        state.handle_key(KeyEvent::new(KeyCode::Char('9'), KeyModifiers::NONE));
        let cancel_act = state.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert_eq!(cancel_act, SettingsPickerAction::Continue);
        assert!(state.wpm_input.is_none());
    }

    #[test]
    fn test_setting_icons_have_terminal_independent_width() {
        use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

        for item in SETTINGS.iter() {
            let icon = item.icon;
            // Strip any variation selector: a terminal that ignores the emoji
            // presentation rule (U+FE0F) then renders the base glyph.
            let bare: String = icon.chars().filter(|c| *c != '\u{fe0f}').collect();
            assert!(
                !icon.contains('\u{200d}'),
                "'{}' uses a ZWJ sequence, whose width varies between terminals",
                item.title
            );
            assert_eq!(
                textfit::width(icon),
                UnicodeWidthStr::width(bare.as_str()),
                "'{}' icon {:?} only reaches its width via U+FE0F",
                item.title,
                icon
            );
            // The column is two cells and `RowLayout` pads to it, so an icon
            // may be narrow, but it must never be wider than the column.
            let glyph = icon.chars().next().unwrap();
            assert!(
                icon.chars().count() == 1,
                "'{}' icon {:?} is more than one glyph",
                item.title,
                icon
            );
            assert!(
                UnicodeWidthChar::width(glyph).unwrap_or(0) <= RowLayout::ICON_W,
                "'{}' icon {:?} is wider than the icon column",
                item.title,
                icon
            );
            assert_eq!(
                textfit::width(&textfit::fit(icon, RowLayout::ICON_W)),
                RowLayout::ICON_W,
                "'{}' icon {:?} does not fill the icon column",
                item.title,
                icon
            );
        }
    }

    /// Every title, description and pill must fit its column in the modal, so
    /// nothing is ever shown as `...`. The popup is 72 cells wide inside an
    /// 80-column terminal, i.e. a 70-cell list. Narrower terminals fall back to
    /// clipping by design; this pins the full-size layout.
    #[test]
    fn test_no_label_is_truncated() {
        let layout = RowLayout::new(70);
        let mut app = App::new("1.1.1", Theme::Cyan);

        let idle = SettingsPickerState::new();
        let reset_done = {
            let mut s = SettingsPickerState::new();
            s.reset_done = true;
            s
        };
        let mic_rescan_done = {
            let mut s = SettingsPickerState::new();
            s.mic_rescan_done = true;
            s
        };
        let reset_armed = {
            let mut s = SettingsPickerState::new();
            s.confirm_defaults = true;
            s
        };
        let reset_committed = {
            let mut s = SettingsPickerState::new();
            s.defaults_done = true;
            s
        };

        let mut clipped: Vec<String> = Vec::new();
        let mut check = |app: &App, state: &SettingsPickerState, kind: SettingKind| {
            let item = SETTINGS.iter().find(|i| i.kind == kind).unwrap();
            let (desc, badge, _) = item.value_and_badge(app, state);
            // The microphone row shows user data (a device name), which can be
            // longer than any column by nature.
            if kind != SettingKind::Microphone && textfit::width(&desc) > layout.desc_w {
                clipped.push(format!(
                    "{:?} ({} > {})",
                    desc,
                    textfit::width(&desc),
                    layout.desc_w
                ));
            }
            assert!(
                textfit::width(item.title) <= layout.title_w,
                "title {:?} does not fit the title column",
                item.title
            );
            assert!(
                textfit::width(badge) <= layout.badge_w,
                "pill {badge:?} is wider than the badge column"
            );
        };

        for flag in [true, false] {
            app.trailing_space = flag;
            check(&app, &idle, SettingKind::TrailingSpace);
            app.serial_collapse = flag;
            check(&app, &idle, SettingKind::SerialCollapse);
            app.spell_command = flag;
            check(&app, &idle, SettingKind::SpellCommand);
            app.middle_click_enabled = flag;
            check(&app, &idle, SettingKind::MiddleClick);
            app.is_muted = flag;
            check(&app, &idle, SettingKind::SoundMute);
        }
        for mode in ["auto", "digits", "words"] {
            app.number_mode = mode.to_string();
            check(&app, &idle, SettingKind::NumberDigits);
        }
        for wpm in [30, 40, 50, 60, 70, 80, 100] {
            app.typing_wpm = wpm;
            check(&app, &idle, SettingKind::TypingWpm);
        }
        // Only the modes the engine round-trips (``t2.OUTPUT_MODES``).
        for mode in ["clipboard", "type", "type_fast"] {
            app.output_mode = mode.to_string();
            check(&app, &idle, SettingKind::OutputMode);
        }
        for mode in [
            "default",
            "casual",
            "autocorrect",
            "aesthetic",
            "gen_z",
            "full",
            "no_terminal_period",
            "no_punctuation",
            "aesthetic_lowercase",
            "lowercase_no_punctuation",
        ] {
            app.punctuation_mode = mode.to_string();
            check(&app, &idle, SettingKind::PunctuationMode);
        }
        for mode in ["off", "inline", "blocks"] {
            app.structure_mode = mode.to_string();
            check(&app, &idle, SettingKind::StructureMode);
        }
        for mode in ["off", "artifacts", "full"] {
            app.cleanup_mode = mode.to_string();
            check(&app, &idle, SettingKind::CleanupMode);
        }
        for mode in ["off", "on"] {
            app.meeting_mode = mode.to_string();
            check(&app, &idle, SettingKind::MeetingMode);
        }
        for minutes in [5u32, 10, 20, 30, 60] {
            app.meeting_spill_minutes = minutes;
            check(&app, &idle, SettingKind::MeetingSpill);
        }
        for theme in [
            Theme::Auto,
            Theme::Green,
            Theme::Cyan,
            Theme::Blue,
            Theme::Magenta,
            Theme::Yellow,
            Theme::Red,
            Theme::White,
        ] {
            app.ui_theme = theme;
            check(&app, &idle, SettingKind::Theme);
        }
        for state in [&idle, &reset_done] {
            check(&app, state, SettingKind::ResetTerminal);
        }
        for state in [&idle, &mic_rescan_done] {
            check(&app, state, SettingKind::RescanMics);
        }
        for state in [&idle, &reset_armed, &reset_committed] {
            check(&app, state, SettingKind::ResetDefaults);
        }

        assert!(clipped.is_empty(), "labels would be clipped: {clipped:?}");
    }

    #[test]
    fn test_meeting_previews_match_the_spec() {
        // Spec: docs/meeting_mode.md, enforced on the Python side by
        // tests/shared/test_config_sync.py (the label helper) and the
        // source-level parity guard there.
        let mut app = App::new("1.1.1", Theme::Cyan);
        let idle = SettingsPickerState::new();

        let mode_item = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::MeetingMode)
            .unwrap();
        let expected = [
            ("off", "Off: dictation only", "[OFF]"),
            ("on", "Long, non-injecting capture", "[ON]"),
        ];
        for (mode, desc, badge) in expected {
            app.meeting_mode = mode.to_string();
            let (got_desc, got_badge, _) = mode_item.value_and_badge(&app, &idle);
            assert_eq!(got_desc, desc, "preview for meeting mode {mode}");
            assert_eq!(got_badge, badge, "badge for meeting mode {mode}");
        }

        let spill_item = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::MeetingSpill)
            .unwrap();
        for minutes in [5u32, 10, 20, 30, 60] {
            app.meeting_spill_minutes = minutes;
            let (desc, badge, _) = spill_item.value_and_badge(&app, &idle);
            assert_eq!(desc, format!("Spills to disk past {minutes} min"));
            assert_eq!(badge, "[SPILL]");
        }

        // The local cycle must only produce values the engine knows, and round-trip.
        app.meeting_mode = "off".to_string();
        app.cycle_meeting_mode();
        assert_eq!(app.meeting_mode, "on");
        app.cycle_meeting_mode();
        assert_eq!(app.meeting_mode, "off");

        app.meeting_spill_minutes = 5;
        app.cycle_meeting_spill();
        assert_eq!(app.meeting_spill_minutes, 10);
        app.cycle_meeting_spill();
        assert_eq!(app.meeting_spill_minutes, 20);
        app.cycle_meeting_spill();
        assert_eq!(app.meeting_spill_minutes, 30);
        app.cycle_meeting_spill();
        assert_eq!(app.meeting_spill_minutes, 60);
        app.cycle_meeting_spill();
        assert_eq!(app.meeting_spill_minutes, 5);
    }

    #[test]
    fn test_structure_previews_match_the_spec() {
        // Spec: docs/formatting.md, enforced on the Python side by
        // tests/shared/test_structure_blocks.py and
        // tests/shared/test_config_sync.py (the downgrade rule).
        let mut app = App::new("1.1.1", Theme::Cyan);
        let idle = SettingsPickerState::new();
        let item = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::StructureMode)
            .unwrap();

        app.structure_mode = "off".to_string();
        let (desc, badge, _) = item.value_and_badge(&app, &idle);
        assert_eq!(desc, "Flat prose (no list formatting)");
        assert_eq!(badge, "[OFF]");

        app.structure_mode = "inline".to_string();
        let (desc, badge, _) = item.value_and_badge(&app, &idle);
        assert_eq!(desc, "Bullets on one line: - one. - two.");
        assert_eq!(badge, "[INLINE]");

        // Configured "blocks" reads differently depending on the output path,
        // because typing downgrades it to inline in the engine.
        app.structure_mode = "blocks".to_string();
        app.output_mode = "clipboard".to_string();
        let (desc, badge, _) = item.value_and_badge(&app, &idle);
        assert_eq!(desc, "Bullets on their own lines");
        assert_eq!(badge, "[BLOCKS]");

        for mode in ["type", "type_fast"] {
            app.output_mode = mode.to_string();
            let (desc, badge, _) = item.value_and_badge(&app, &idle);
            assert_eq!(desc, "Typed text stays inline", "output mode {mode}");
            assert_eq!(badge, "[PASTE]", "output mode {mode}");
        }

        // The optimistic local cycle must only ever produce modes the engine
        // knows, and must round-trip.
        app.structure_mode = "off".to_string();
        app.cycle_structure_mode();
        assert_eq!(app.structure_mode, "inline");
        app.cycle_structure_mode();
        assert_eq!(app.structure_mode, "blocks");
        app.cycle_structure_mode();
        assert_eq!(app.structure_mode, "off");
    }

    #[test]
    fn test_formatter_previews_match_the_spec() {
        // Spec: docs/plan-on-device-formatter.md, enforced on the Python side by
        // tests/shared/test_formatter.py and tests/shared/test_config_sync.py.
        let mut app = App::new("1.1.1", Theme::Cyan);
        let idle = SettingsPickerState::new();

        let formatter = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::Formatter)
            .unwrap();
        app.formatter = "off".to_string();
        let (desc, badge, _) = formatter.value_and_badge(&app, &idle);
        assert_eq!(desc, "Off: deterministic cleanup only");
        assert_eq!(badge, "[OFF]");
        app.formatter = "on".to_string();
        let (desc, badge, _) = formatter.value_and_badge(&app, &idle);
        assert_eq!(desc, "Rewrites the transcript on this machine");
        assert_eq!(badge, "[ON]");

        let model = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::FormatterModel)
            .unwrap();
        for value in ["s1-mini", "llama-server"] {
            app.formatter_model = value.to_string();
            let (desc, badge, _) = model.value_and_badge(&app, &idle);
            assert_eq!(desc, format!("Model: {value}"));
            assert_eq!(badge, "[MODEL]");
        }

        let style = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::FormatterStyle)
            .unwrap();
        for value in ["casual", "semi-casual", "semi-formal", "formal"] {
            app.formatter_style = value.to_string();
            let (desc, badge, _) = style.value_and_badge(&app, &idle);
            assert_eq!(desc, format!("Writing style: {value}"));
            assert_eq!(badge, "[STYLE]");
        }

        let context = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::FormatterContext)
            .unwrap();
        for value in ["general", "email"] {
            app.formatter_context = value.to_string();
            let (desc, badge, _) = context.value_and_badge(&app, &idle);
            assert_eq!(desc, format!("Context: {value}"));
            assert_eq!(badge, "[CONTEXT]");
        }

        // The optimistic local cycles must only ever produce values the engine
        // knows, and must round-trip.
        app.formatter = "off".to_string();
        app.cycle_formatter();
        assert_eq!(app.formatter, "on");
        app.cycle_formatter();
        assert_eq!(app.formatter, "off");

        // One model today, and the cycle is built over a list so a second is data
        // rather than a refactor. Cycling a one-element list is a no-op rather
        // than a special case, and the engine is authoritative anyway - its cfg
        // reply overwrites whatever this produced.
        app.formatter_model = "s1-mini".to_string();
        app.cycle_formatter_model();
        assert_eq!(app.formatter_model, "s1-mini");
        app.cycle_formatter_model();
        assert_eq!(app.formatter_model, "s1-mini");

        app.formatter_style = "casual".to_string();
        app.cycle_formatter_style();
        assert_eq!(app.formatter_style, "semi-casual");
        app.cycle_formatter_style();
        assert_eq!(app.formatter_style, "semi-formal");
        app.cycle_formatter_style();
        assert_eq!(app.formatter_style, "formal");
        app.cycle_formatter_style();
        assert_eq!(app.formatter_style, "casual");

        app.formatter_context = "general".to_string();
        app.cycle_formatter_context();
        assert_eq!(app.formatter_context, "email");
        app.cycle_formatter_context();
        assert_eq!(app.formatter_context, "general");
    }

    #[test]
    fn test_cleanup_previews_match_the_spec() {
        // Spec: docs/cleanup_modes.md, enforced on the Python side by
        // tests/shared/test_cleanup_modes.py.
        let mut app = App::new("1.1.1", Theme::Cyan);
        let idle = SettingsPickerState::new();
        let item = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::CleanupMode)
            .unwrap();

        let expected = [
            ("off", "Keeps every word", "[OFF]"),
            ("artifacts", "Removes noise only", "[NOISE]"),
            ("full", "Resolves corrections", "[FULL]"),
        ];
        for (mode, desc, badge) in expected {
            app.cleanup_mode = mode.to_string();
            let (got_desc, got_badge, _) = item.value_and_badge(&app, &idle);
            assert_eq!(got_desc, desc, "preview for cleanup mode {mode}");
            assert_eq!(got_badge, badge, "badge for cleanup mode {mode}");
        }

        // The local cycle must only produce modes the engine knows, and round-trip.
        app.cleanup_mode = "off".to_string();
        app.cycle_cleanup_mode();
        assert_eq!(app.cleanup_mode, "artifacts");
        app.cycle_cleanup_mode();
        assert_eq!(app.cleanup_mode, "full");
        app.cycle_cleanup_mode();
        assert_eq!(app.cleanup_mode, "off");
    }

    #[test]
    fn test_preset_previews_match_the_spec() {
        // Spec: docs/mode_presets.md, enforced end-to-end by
        // tests/shared/test_mode_presets.py (which greps these exact strings).
        let app = App::new("1.1.1", Theme::Cyan);
        let state = SettingsPickerState::new();
        let item = SETTINGS
            .iter()
            .find(|i| i.kind == SettingKind::PunctuationMode)
            .unwrap();
        let expected = [
            ("full", "\"Hey, how are you? I'm good.\"", "[DEFAULT]"),
            (
                "no_terminal_period",
                "\"Hey, how are you? I'm good\"",
                "[CASUAL]",
            ),
            ("no_punctuation", "\"Hey how are you I'm good\"", "[PHONE]"),
            (
                "aesthetic_lowercase",
                "\"hey, how are you? i'm good\"",
                "[AESTH]",
            ),
            ("gen_z", "\"hey how are you i'm good\"", "[GEN Z]"),
        ];

        let mut app = app;
        let mut previews: Vec<String> = Vec::new();
        for (mode, desc, badge) in expected {
            app.punctuation_mode = mode.to_string();
            let (got_desc, got_badge, _) = item.value_and_badge(&app, &state);
            assert_eq!(got_desc, desc, "preview for preset {mode}");
            assert_eq!(got_badge, badge, "badge for preset {mode}");
            previews.push(got_desc);
        }

        // ...and the presets must stay tellable apart from each other.
        let unique: std::collections::HashSet<&String> = previews.iter().collect();
        assert_eq!(
            unique.len(),
            previews.len(),
            "two presets show the same preview: {previews:?}"
        );
    }

    #[test]
    fn test_setting_rows_share_one_right_edge() {
        let app = App::new("1.1.1", Theme::Cyan);

        // Exercise the pills that only appear in particular states.
        let mut armed = SettingsPickerState::new();
        armed.confirm_defaults = true;
        let mut done = SettingsPickerState::new();
        done.reset_done = true;
        done.defaults_done = true;

        for state in [SettingsPickerState::new(), armed, done] {
            for total in [34usize, 44, 64, 70, 76, 96, 130] {
                let layout = RowLayout::new(total);
                assert_eq!(
                    layout.row_width(),
                    total,
                    "settings row overflows a {total}-column list"
                );
                // The right margin is left blank, so the rendered row stops
                // exactly one column short of the border on every line.
                let expected = layout.row_width() - RowLayout::MARGIN;
                for item in SETTINGS.iter() {
                    let (val, badge, color) = item.value_and_badge(&app, &state);
                    for is_selected in [true, false] {
                        let line = setting_row(
                            item,
                            &val,
                            badge,
                            color,
                            is_selected,
                            Color::Cyan,
                            &layout,
                        );
                        assert_eq!(
                            line.width(),
                            expected,
                            "'{}' row is ragged at width {total}",
                            item.title
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn test_rescan_mics_is_findable_and_not_destructive() {
        let mut state = SettingsPickerState::new();
        state.query = "mic".to_string();
        state.update_filter();
        let pos = state
            .filtered_indices
            .iter()
            .position(|&i| SETTINGS[i].kind == SettingKind::RescanMics)
            .expect("reset-microphones must be findable by search");
        state.selected_index = pos;

        // One activation is enough: unlike the factory reset there is nothing
        // destructive about re-enumerating devices, so it must not need arming.
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(
            action,
            SettingsPickerAction::Toggle(SettingKind::RescanMics)
        );
        assert!(!state.confirm_defaults);
        assert!(!state.mic_rescan_done);
    }

    #[test]
    fn test_reset_defaults_requires_confirmation() {
        let mut state = SettingsPickerState::new();
        state.query = "reset".to_string();
        state.update_filter();
        let pos = state
            .filtered_indices
            .iter()
            .position(|&i| SETTINGS[i].kind == SettingKind::ResetDefaults)
            .expect("reset-to-defaults must be findable by search");
        state.selected_index = pos;

        // First activation only arms the destructive reset.
        let action = state.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(
            action,
            SettingsPickerAction::Toggle(SettingKind::ResetDefaults)
        );
        assert!(!state.confirm_reset_defaults());
        assert!(state.confirm_defaults);
        assert!(!state.defaults_done);

        // The second consecutive activation commits it.
        assert!(state.confirm_reset_defaults());
        assert!(state.defaults_done);
        assert!(!state.confirm_defaults);

        // ...but a stray keystroke in between disarms it instead of wiping.
        assert!(!state.confirm_reset_defaults());
        state.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert!(!state.confirm_defaults);
    }
}

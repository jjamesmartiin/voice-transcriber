//! IPC protocol with the Python backend.
//!
//! Transport: a Unix domain socket created by the Python side. The Python
//! engine remains the source of truth (hotkeys, audio, ASR, clipboard); this
//! process is a pure view + input device.
//!
//! Framing: newline-delimited JSON, one object per line.
//!
//! Python -> Rust:
//!   {"t":"state","state":"READY","sub":""}
//!   {"t":"meeting_text","text":"accumulated transcript so far"}
//!   {"t":"vu","level":0.42}
//!   {"t":"vu","level":0.42,"levels":[{"i":4,"level":0.0},{"i":5,"level":0.44}]}
//!   {"t":"cfg","mic":"...","secondary":"...","backend":"cohere",
//!    "muted":true,"auto_type":false,"sound_theme":"proximity","ui_theme":"auto",
//!    "formatter":"off","formatter_model":"s1-mini","formatter_style":"semi-formal",
//!    "formatter_context":"general"}
//!   {"t":"tx","text":"...","rec":3.2,"proc":1.35,"ready":0.62,"status":"typed"}
//!   {"t":"ev","title":"...","message":"...","level":"info"}
//!   {"t":"suspend"} / {"t":"resume"} / {"t":"quit"}
//!
//! Rust -> Python:
//!   {"t":"cmd","cmd":"toggle_record"}  (and friends)

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::sync::mpsc;
use std::thread;

use serde::{Deserialize, Serialize};

use crate::app::{App, Level, OutStatus};

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq)]
pub struct AudioDeviceInfo {
    pub index: usize,
    pub name: String,
    #[serde(default)]
    pub display_name: Option<String>,
    #[serde(default)]
    pub channels: usize,
    #[serde(default)]
    pub is_default: bool,
    #[serde(default)]
    pub is_active: bool,
}

/// One device's live input level, as reported while the mic picker is open.
/// `i` is the PortAudio device index (the same one `start_mic_monitor` takes).
#[derive(Deserialize, Serialize, Clone, Copy, Debug, PartialEq)]
pub struct VuLevel {
    pub i: usize,
    pub level: f32,
}

// `cfg` mirrors Python's optional config fields one-for-one, so it is
// deliberately much larger than the other variants. One `Wire` is handled per
// channel message and `IpcEvent` already boxes it, so boxing the config fields
// individually would add indirection without meaningfully reducing stack use.
#[allow(clippy::large_enum_variant)]
/// The speaker-count hint is either the string ``"auto"`` or an integer
/// ``2``-``8``. Serde cannot decode that into one concrete type, so it is a
/// union; `App` stores the canonical string form.
#[derive(Deserialize, Debug, Clone, PartialEq, Eq)]
#[serde(untagged)]
pub enum SpeakerCount {
    Auto(String),
    Count(u32),
}

impl SpeakerCount {
    /// The canonical string form the settings row and status carry.
    pub fn as_string(&self) -> String {
        match self {
            SpeakerCount::Auto(s) => s.clone(),
            SpeakerCount::Count(n) => n.to_string(),
        }
    }
}

#[allow(clippy::large_enum_variant)] // protocol-shaped: one `Wire` per channel
                                     // message and the channel holds at most a frame's worth, so
                                     // boxing the growable `Cfg` fields would add an allocation per
                                     // message for negligible stack savings.
#[derive(Deserialize, Debug)]
#[serde(tag = "t")]
pub enum Wire {
    #[serde(rename = "devices")]
    Devices {
        /// Absent means no update; `Some([])` is a legitimate empty result.
        #[serde(default)]
        devices: Option<Vec<AudioDeviceInfo>>,
    },
    #[serde(rename = "state")]
    State {
        state: String,
        #[serde(default)]
        sub: String,
    },
    /// Live transcript streamed while a meeting records. `text` is the whole
    /// accumulated transcript so far, not a delta, so the screen can simply
    /// show its tail.
    #[serde(rename = "meeting_text")]
    MeetingText {
        #[serde(default)]
        text: String,
    },
    #[serde(rename = "vu")]
    Vu {
        level: f32,
        /// Per-device levels for the mic picker. Absent on a scalar-only update,
        /// which the picker reads as "leave the per-device rows alone"; an
        /// explicit empty list means "no device is monitored".
        #[serde(default)]
        levels: Option<Vec<VuLevel>>,
    },
    #[serde(rename = "cfg")]
    Cfg {
        #[serde(default)]
        mic: Option<String>,
        #[serde(default)]
        secondary: Option<String>,
        #[serde(default)]
        backend: Option<String>,
        #[serde(default)]
        muted: Option<bool>,
        #[serde(default)]
        auto_type: Option<bool>,
        #[serde(default)]
        output_mode: Option<String>,
        #[serde(default)]
        sound_theme: Option<String>,
        #[serde(default)]
        ui_theme: Option<String>,
        #[serde(default)]
        punctuation_mode: Option<String>,
        /// Configured list-formatting mode: "off", "inline" or "blocks". The
        /// engine reports what the user chose; the *effective* mode can be
        /// "inline" while typing, because a newline is an Enter keypress.
        #[serde(default)]
        structure_mode: Option<String>,
        /// Configured formatter toggle: "off" or "on". The engine only runs a
        /// rewrite when the user opted in *and* deterministic cleanup is on.
        #[serde(default)]
        formatter: Option<String>,
        /// Configured formatter backend identifier, e.g. "s1-mini".
        #[serde(default)]
        formatter_model: Option<String>,
        /// Configured formatter writing style, e.g. "semi-formal".
        #[serde(default)]
        formatter_style: Option<String>,
        /// Configured formatter context, e.g. "general".
        #[serde(default)]
        formatter_context: Option<String>,
        /// Configured cleanup mode: "off", "artifacts" or "full" — how much of
        /// the pass may change the words (see docs/cleanup_modes.md).
        #[serde(default)]
        cleanup_mode: Option<String>,
        /// Configured meeting-mode toggle: "off" or "on". Meeting capture is a
        /// second, non-injecting lifecycle (see docs/meeting_mode.md).
        #[serde(default)]
        meeting_mode: Option<String>,
        /// Meeting capture in-memory spill threshold, in minutes (1-240).
        #[serde(default)]
        meeting_spill_minutes: Option<u32>,
        /// Configured diarization toggle: "off" or "on". Meeting transcripts
        /// are labelled per speaker when on (see docs/meeting_mode.md).
        #[serde(default)]
        diarization: Option<String>,
        /// Speaker-count hint: the string "auto" or an integer 2-8. The wire
        /// form is genuinely both, so it needs a union rather than a String.
        #[serde(default)]
        diarization_speakers: Option<SpeakerCount>,
        /// Configured diarization model registry id, e.g. "diarization".
        #[serde(default)]
        diarization_model: Option<String>,
        /// Where the finished transcript is written (repo-relative by default).
        #[serde(default)]
        meeting_output_dir: Option<String>,
        /// The artifact form: "text", "json" or "markdown".
        #[serde(default)]
        meeting_output_format: Option<String>,
        /// The live speakers map: ordered names, index *i* names speaker *i*.
        /// Transcript metadata, not config (see docs/meeting_mode.md).
        #[serde(default)]
        speakers: Option<Vec<String>>,
        #[serde(default)]
        trailing_space: Option<bool>,
        #[serde(default)]
        auto_punctuate: Option<bool>,
        #[serde(default)]
        number_digits: Option<bool>,
        #[serde(default)]
        number_mode: Option<String>,
        #[serde(default)]
        serial_collapse: Option<bool>,
        #[serde(default)]
        spell_command: Option<bool>,
        #[serde(default)]
        middle_click_enabled: Option<bool>,
        #[serde(default)]
        typing_wpm: Option<u32>,
        /// Canonical push-to-talk chord spellings, e.g. "alt+shift".
        #[serde(default)]
        hotkeys: Option<Vec<String>>,
    },
    #[serde(rename = "tx")]
    Tx {
        text: String,
        #[serde(default)]
        rec: f32,
        #[serde(default)]
        proc: f32,
        #[serde(default)]
        ready: f32,
        #[serde(default)]
        status: Option<String>,
        #[serde(default)]
        time_saved: Option<f32>,
        #[serde(default)]
        session_time_saved: Option<f32>,
        #[serde(default)]
        lifetime_time_saved: Option<f32>,
    },
    #[serde(rename = "ev")]
    Ev {
        title: String,
        message: String,
        #[serde(default)]
        level: Option<String>,
    },
    #[serde(rename = "suspend")]
    Suspend,
    #[serde(rename = "resume")]
    Resume,
    #[serde(rename = "header")]
    Header,
    #[serde(rename = "quit")]
    Quit,
}

#[allow(clippy::large_enum_variant)] // one `Wire` per channel message; the
                                     // channel holds at most a frame's worth, so boxing would only add an allocation
                                     // per message for negligible stack savings.
pub enum IpcEvent {
    Wire(Box<Wire>),
    Closed,
}

pub const MAX_LINE_BYTES: usize = 65536;

fn parse_cfg_tolerant(map: serde_json::Map<String, serde_json::Value>) -> Wire {
    Wire::Cfg {
        mic: map.get("mic").and_then(|v| v.as_str().map(String::from)),
        secondary: map.get("secondary").and_then(|v| v.as_str().map(String::from)),
        backend: map.get("backend").and_then(|v| v.as_str().map(String::from)),
        muted: map.get("muted").and_then(|v| v.as_bool()),
        auto_type: map.get("auto_type").and_then(|v| v.as_bool()),
        output_mode: map.get("output_mode").and_then(|v| v.as_str().map(String::from)),
        sound_theme: map.get("sound_theme").and_then(|v| v.as_str().map(String::from)),
        ui_theme: map.get("ui_theme").and_then(|v| v.as_str().map(String::from)),
        punctuation_mode: map.get("punctuation_mode").and_then(|v| v.as_str().map(String::from)),
        structure_mode: map.get("structure_mode").and_then(|v| v.as_str().map(String::from)),
        formatter: map.get("formatter").and_then(|v| v.as_str().map(String::from)),
        formatter_model: map.get("formatter_model").and_then(|v| v.as_str().map(String::from)),
        formatter_style: map.get("formatter_style").and_then(|v| v.as_str().map(String::from)),
        formatter_context: map.get("formatter_context").and_then(|v| v.as_str().map(String::from)),
        cleanup_mode: map.get("cleanup_mode").and_then(|v| v.as_str().map(String::from)),
        meeting_mode: map.get("meeting_mode").and_then(|v| v.as_str().map(String::from)),
        meeting_spill_minutes: map.get("meeting_spill_minutes").and_then(|v| v.as_u64().map(|n| n as u32)),
        diarization: map.get("diarization").and_then(|v| v.as_str().map(String::from)),
        diarization_speakers: map.get("diarization_speakers").and_then(|v| {
            v.as_str()
                .map(|s| SpeakerCount::Auto(s.to_string()))
                .or_else(|| v.as_u64().map(|n| SpeakerCount::Count(n as u32)))
        }),
        diarization_model: map.get("diarization_model").and_then(|v| v.as_str().map(String::from)),
        meeting_output_dir: map.get("meeting_output_dir").and_then(|v| v.as_str().map(String::from)),
        meeting_output_format: map.get("meeting_output_format").and_then(|v| v.as_str().map(String::from)),
        speakers: map.get("speakers").and_then(|v| {
            v.as_array().map(|arr| {
                arr.iter().filter_map(|item| item.as_str().map(String::from)).collect()
            })
        }),
        trailing_space: map.get("trailing_space").and_then(|v| v.as_bool()),
        auto_punctuate: map.get("auto_punctuate").and_then(|v| v.as_bool()),
        number_digits: map.get("number_digits").and_then(|v| v.as_bool()),
        number_mode: map.get("number_mode").and_then(|v| v.as_str().map(String::from)),
        serial_collapse: map.get("serial_collapse").and_then(|v| v.as_bool()),
        spell_command: map.get("spell_command").and_then(|v| v.as_bool()),
        middle_click_enabled: map.get("middle_click_enabled").and_then(|v| v.as_bool()),
        typing_wpm: map.get("typing_wpm").and_then(|v| v.as_u64().map(|n| n as u32)),
        hotkeys: map.get("hotkeys").and_then(|v| {
            v.as_array().map(|arr| {
                arr.iter().filter_map(|item| item.as_str().map(String::from)).collect()
            })
        }),
    }
}

pub fn parse_wire(text: &str) -> Result<Wire, String> {
    match serde_json::from_str::<Wire>(text) {
        Ok(w) => Ok(w),
        Err(e) => {
            if let Ok(serde_json::Value::Object(map)) = serde_json::from_str::<serde_json::Value>(text) {
                if map.get("t").and_then(|v| v.as_str()) == Some("cfg") {
                    return Ok(parse_cfg_tolerant(map));
                }
            }
            Err(e.to_string())
        }
    }
}

/// Connect and spawn a reader thread. Returns the receiver plus a writer handle
/// (a clone of the socket) for sending commands back to Python.
pub fn connect(path: &str) -> std::io::Result<(mpsc::Receiver<IpcEvent>, UnixStream)> {
    let stream = UnixStream::connect(path)?;
    stream.set_write_timeout(Some(std::time::Duration::from_secs(5)))?;
    let reader = stream.try_clone()?;
    reader.set_read_timeout(Some(std::time::Duration::from_secs(5)))?;
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut buf = BufReader::new(reader);
        let mut line_bytes = Vec::new();
        loop {
            line_bytes.clear();
            match buf.read_until(b'\n', &mut line_bytes) {
                Ok(0) => {
                    let _ = tx.send(IpcEvent::Closed);
                    break;
                }
                Ok(_) => {
                    if line_bytes.len() > MAX_LINE_BYTES {
                        eprintln!("vt-tui: line exceeded MAX_LINE_BYTES, skipping");
                        continue;
                    }
                    let line = String::from_utf8_lossy(&line_bytes);
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    match parse_wire(trimmed) {
                        Ok(w) => {
                            if tx.send(IpcEvent::Wire(Box::new(w))).is_err() {
                                break;
                            }
                        }
                        Err(e) => {
                            eprintln!("vt-tui: bad IPC message: {e}: {trimmed}");
                        }
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::TimedOut || e.kind() == std::io::ErrorKind::WouldBlock => {
                    continue;
                }
                Err(_) => {
                    let _ = tx.send(IpcEvent::Closed);
                    break;
                }
            }
        }
    });
    Ok((rx, stream))
}

/// Send a simple command with no payload. Returns an error when the writer is
/// dead (broken pipe / closed socket) so the caller can treat it as a
/// disconnect instead of silently losing the command.
pub fn send_cmd(stream: &UnixStream, cmd: &str) -> std::io::Result<()> {
    let line = format!("{{\"t\":\"cmd\",\"cmd\":\"{cmd}\"}}\n");
    write_line(stream, &line)
}

/// Send a command with a string property.
pub fn send_cmd_value(stream: &UnixStream, cmd: &str, key: &str, val: &str) -> std::io::Result<()> {
    let mut extra = serde_json::Map::new();
    extra.insert(key.to_string(), serde_json::Value::String(val.to_string()));
    send_cmd_json(stream, cmd, serde_json::Value::Object(extra))
}

/// Send a command object assembled by serde.
///
/// Audio device names routinely contain quotes, backslashes and commas, so the
/// payload must be escaped rather than interpolated into a format string.
pub fn send_cmd_json(
    stream: &UnixStream,
    cmd: &str,
    extra: serde_json::Value,
) -> std::io::Result<()> {
    let mut obj = serde_json::Map::new();
    obj.insert(
        "t".to_string(),
        serde_json::Value::String("cmd".to_string()),
    );
    obj.insert(
        "cmd".to_string(),
        serde_json::Value::String(cmd.to_string()),
    );
    if let serde_json::Value::Object(extra) = extra {
        obj.extend(extra);
    }
    let line = format!("{}\n", serde_json::Value::Object(obj));
    write_line(stream, &line)
}

fn write_line(stream: &UnixStream, line: &str) -> std::io::Result<()> {
    let mut w = stream;
    w.write_all(line.as_bytes())?;
    w.flush()
}

/// Send a fire-and-forget command, treating a dead writer as a disconnect.
///
/// Every modal arm that talks to the engine must do the same two things: skip
/// the send when there is no engine (demo mode), and set `should_quit` when the
/// socket is gone so the modal does not spin forever on a dead writer. Open-
/// coding that pair invites a missed `.is_err()`; this is the one place it lives.
/// A `None` writer is expected (demo mode) and is not a disconnect.
pub fn fire(writer: Option<&UnixStream>, cmd: &str, app: &mut App) {
    if let Some(w) = writer {
        if send_cmd(w, cmd).is_err() {
            app.should_quit = true;
        }
    }
}

/// As [`fire`], with one string property.
pub fn fire_value(writer: Option<&UnixStream>, cmd: &str, key: &str, val: &str, app: &mut App) {
    if let Some(w) = writer {
        if send_cmd_value(w, cmd, key, val).is_err() {
            app.should_quit = true;
        }
    }
}

/// As [`fire`], with a JSON payload (device names and speaker lists).
pub fn fire_json(writer: Option<&UnixStream>, cmd: &str, payload: serde_json::Value, app: &mut App) {
    if let Some(w) = writer {
        if send_cmd_json(w, cmd, payload).is_err() {
            app.should_quit = true;
        }
    }
}

pub fn status_from_wire(s: Option<&str>) -> OutStatus {
    match s.unwrap_or("copied") {
        "typed" => OutStatus::Typed,
        "error" => OutStatus::CopyError,
        _ => OutStatus::Copied,
    }
}

pub fn level_from_wire(s: Option<&str>) -> Level {
    match s.unwrap_or("info") {
        "success" => Level::Success,
        "warning" => Level::Warning,
        "error" => Level::Error,
        _ => Level::Info,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The `tx` (transcription) wire message must tolerate both directions of
    /// version skew, since the Python engine and this binary are upgraded
    /// independently (the engine may be restarted at a different time than the
    /// TUI is rebuilt, and a `VT_TUI_BIN` dev build can be older than either):
    ///   * an older engine that never sends `lifetime_time_saved`
    ///   * a newer engine that sends fields this binary does not know about
    #[test]
    fn tx_message_parses_without_lifetime_time_saved() {
        let json = r#"{"t":"tx","text":"hello","rec":1.5,"proc":0.5,"ready":0.2,"status":"copied","time_saved":12.0,"session_time_saved":105.0}"#;
        match serde_json::from_str::<Wire>(json).expect("old engine payload must parse") {
            Wire::Tx {
                lifetime_time_saved,
                session_time_saved,
                text,
                ..
            } => {
                assert_eq!(text, "hello");
                assert_eq!(session_time_saved, Some(105.0));
                assert_eq!(lifetime_time_saved, None);
            }
            other => panic!("expected Tx, got {other:?}"),
        }
    }

    #[test]
    fn tx_message_parses_with_lifetime_time_saved() {
        let json = r#"{"t":"tx","text":"hi","rec":1.0,"proc":0.1,"ready":0.1,"status":"typed","time_saved":12.0,"session_time_saved":105.0,"lifetime_time_saved":3661.0}"#;
        match serde_json::from_str::<Wire>(json).expect("new engine payload must parse") {
            Wire::Tx {
                lifetime_time_saved,
                ..
            } => {
                assert_eq!(lifetime_time_saved, Some(3661.0));
            }
            other => panic!("expected Tx, got {other:?}"),
        }
    }

    #[test]
    fn tx_message_ignores_unknown_future_fields() {
        // A newer engine adding a field must not break an older TUI.
        let json = r#"{"t":"tx","text":"hi","rec":1.0,"proc":0.1,"ready":0.1,"status":"copied","time_saved":12.0,"session_time_saved":105.0,"lifetime_time_saved":3661.0,"words_per_second":9.9}"#;
        assert!(serde_json::from_str::<Wire>(json).is_ok());
    }

    /// The engine streams the accumulated meeting transcript while a capture
    /// records; `text` defaults to empty so a minimal producer still parses.
    #[test]
    fn meeting_text_message_carries_the_accumulated_transcript() {
        let json = r#"{"t":"meeting_text","text":"Alice: hello\nBob: hi"}"#;
        match serde_json::from_str::<Wire>(json).expect("meeting_text must parse") {
            Wire::MeetingText { text } => assert_eq!(text, "Alice: hello\nBob: hi"),
            other => panic!("expected MeetingText, got {other:?}"),
        }

        // An empty payload is legitimate (a fresh capture before any words).
        match serde_json::from_str::<Wire>(r#"{"t":"meeting_text"}"#)
            .expect("empty meeting_text must parse")
        {
            Wire::MeetingText { text } => assert_eq!(text, ""),
            other => panic!("expected MeetingText, got {other:?}"),
        }
    }

    /// The picker used to receive a single scalar; it must still understand it.
    #[test]
    fn vu_message_tolerates_a_scalar_only_producer() {
        match serde_json::from_str::<Wire>(r#"{"t":"vu","level":0.42}"#)
            .expect("scalar vu must parse")
        {
            Wire::Vu { level, levels } => {
                assert_eq!(level, 0.42);
                assert_eq!(levels, None, "no per-device levels were supplied");
            }
            other => panic!("expected Vu, got {other:?}"),
        }
    }

    #[test]
    fn vu_message_carries_every_monitored_device() {
        let json = r#"{"t":"vu","level":0.42,"levels":[{"i":4,"level":0.0},{"i":5,"level":0.44}]}"#;
        match serde_json::from_str::<Wire>(json).expect("multi-device vu must parse") {
            Wire::Vu { level, levels } => {
                assert_eq!(level, 0.42);
                assert_eq!(
                    levels,
                    Some(vec![
                        VuLevel { i: 4, level: 0.0 },
                        VuLevel { i: 5, level: 0.44 },
                    ])
                );
            }
            other => panic!("expected Vu, got {other:?}"),
        }
    }

    /// The push-to-talk chord list rides along on the same `cfg` payload as
    /// `typing_wpm`; an older engine that omits it must still parse.
    #[test]
    fn cfg_message_carries_hotkeys_or_omits_them() {
        let with = r#"{"t":"cfg","hotkeys":["alt+shift","f13"]}"#;
        match serde_json::from_str::<Wire>(with).expect("hotkey cfg must parse") {
            Wire::Cfg { hotkeys, .. } => {
                assert_eq!(
                    hotkeys,
                    Some(vec!["alt+shift".to_string(), "f13".to_string()])
                );
            }
            other => panic!("expected Cfg, got {other:?}"),
        }

        let without = r#"{"t":"cfg","typing_wpm":40}"#;
        match serde_json::from_str::<Wire>(without).expect("old cfg must parse") {
            Wire::Cfg { hotkeys, .. } => assert_eq!(hotkeys, None),
            other => panic!("expected Cfg, got {other:?}"),
        }
    }

    #[test]
    fn send_cmd_json_escapes_device_names() {
        // A name containing a quote must not terminate the JSON string early.
        let line = command_line(
            "set_device",
            serde_json::json!({ "device": "Mic \"Pro\" \\2" }),
        );
        let parsed: serde_json::Value = serde_json::from_str(&line).expect("must stay valid JSON");
        assert_eq!(parsed["t"], "cmd");
        assert_eq!(parsed["cmd"], "set_device");
        assert_eq!(parsed["device"], "Mic \"Pro\" \\2");
    }

    /// Mirror of `send_cmd_json` that returns the line instead of writing it.
    fn command_line(cmd: &str, extra: serde_json::Value) -> String {
        let mut obj = serde_json::Map::new();
        obj.insert(
            "t".to_string(),
            serde_json::Value::String("cmd".to_string()),
        );
        obj.insert(
            "cmd".to_string(),
            serde_json::Value::String(cmd.to_string()),
        );
        if let serde_json::Value::Object(extra) = extra {
            obj.extend(extra);
        }
        serde_json::Value::Object(obj).to_string()
    }

    #[test]
    fn tx_message_tolerates_only_the_required_text_field() {
        // Every numeric field is `#[serde(default)]`, so a minimal producer works.
        let json = r#"{"t":"tx","text":"minimal"}"#;
        match serde_json::from_str::<Wire>(json).expect("minimal payload must parse") {
            Wire::Tx {
                lifetime_time_saved,
                time_saved,
                session_time_saved,
                ..
            } => {
                assert_eq!(lifetime_time_saved, None);
                assert_eq!(time_saved, None);
                assert_eq!(session_time_saved, None);
            }
            other => panic!("expected Tx, got {other:?}"),
        }
    }

    #[test]
    fn bad_field_type_in_cfg_does_not_drop_other_fields() {
        let bad = r#"{"t":"cfg","mic":"Yeti","muted":true,"diarization_speakers":2.5}"#;
        match parse_wire(bad).expect("tolerant cfg parser must succeed") {
            Wire::Cfg { mic, muted, diarization_speakers, .. } => {
                assert_eq!(mic, Some("Yeti".to_string()));
                assert_eq!(muted, Some(true));
                assert_eq!(diarization_speakers, None);
            }
            other => panic!("expected Cfg, got {other:?}"),
        }
    }

    #[test]
    fn invalid_utf8_byte_lossy_decoding() {
        let bytes = b"{\"t\":\"cfg\",\"mic\":\"test\xffmic\"}\n";
        let line = String::from_utf8_lossy(bytes);
        let parsed = parse_wire(line.trim());
        assert!(parsed.is_ok());
    }
}

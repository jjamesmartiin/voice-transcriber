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
//!   {"t":"vu","level":0.42}
//!   {"t":"vu","level":0.42,"levels":[{"i":4,"level":0.0},{"i":5,"level":0.44}]}
//!   {"t":"cfg","mic":"...","secondary":"...","backend":"cohere",
//!    "muted":true,"auto_type":false,"sound_theme":"proximity","ui_theme":"auto"}
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

use crate::app::{Level, OutStatus};

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

#[derive(Deserialize, Debug)]
#[serde(tag = "t")]
pub enum Wire {
    #[serde(rename = "devices")]
    Devices {
        devices: Vec<AudioDeviceInfo>,
    },
    #[serde(rename = "state")]
    State {
        state: String,
        #[serde(default)]
        sub: String,
    },
    #[serde(rename = "vu")]
    Vu {
        level: f32,
        /// Per-device levels for the mic picker. Absent (empty) on a
        /// scalar-only update, which the picker reads as "nothing monitored".
        #[serde(default)]
        levels: Vec<VuLevel>,
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

pub enum IpcEvent {
    Wire(Wire),
    Closed,
}

/// Connect and spawn a reader thread. Returns the receiver plus a writer handle
/// (a clone of the socket) for sending commands back to Python.
pub fn connect(path: &str) -> std::io::Result<(mpsc::Receiver<IpcEvent>, UnixStream)> {
    let stream = UnixStream::connect(path)?;
    let reader = stream.try_clone()?;
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut buf = BufReader::new(reader);
        let mut line = String::new();
        loop {
            line.clear();
            match buf.read_line(&mut line) {
                Ok(0) => {
                    let _ = tx.send(IpcEvent::Closed);
                    break;
                }
                Ok(_) => {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<Wire>(trimmed) {
                        Ok(w) => {
                            if tx.send(IpcEvent::Wire(w)).is_err() {
                                break;
                            }
                        }
                        Err(e) => {
                            eprintln!("vt-tui: bad IPC message: {e}: {trimmed}");
                        }
                    }
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

/// Send a simple command with no payload.
pub fn send_cmd(stream: &UnixStream, cmd: &str) {
    let line = format!("{{\"t\":\"cmd\",\"cmd\":\"{cmd}\"}}\n");
    let mut w = stream;
    let _ = w.write_all(line.as_bytes());
    let _ = w.flush();
}

/// Send a command with a string property.
pub fn send_cmd_value(stream: &UnixStream, cmd: &str, key: &str, val: &str) {
    let mut extra = serde_json::Map::new();
    extra.insert(key.to_string(), serde_json::Value::String(val.to_string()));
    send_cmd_json(stream, cmd, serde_json::Value::Object(extra));
}

/// Send a command object assembled by serde.
///
/// Audio device names routinely contain quotes, backslashes and commas, so the
/// payload must be escaped rather than interpolated into a format string.
pub fn send_cmd_json(stream: &UnixStream, cmd: &str, extra: serde_json::Value) {
    let mut obj = serde_json::Map::new();
    obj.insert("t".to_string(), serde_json::Value::String("cmd".to_string()));
    obj.insert("cmd".to_string(), serde_json::Value::String(cmd.to_string()));
    if let serde_json::Value::Object(extra) = extra {
        obj.extend(extra);
    }
    let line = format!("{}\n", serde_json::Value::Object(obj));
    let mut w = stream;
    let _ = w.write_all(line.as_bytes());
    let _ = w.flush();
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
            Wire::Tx { lifetime_time_saved, session_time_saved, text, .. } => {
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
            Wire::Tx { lifetime_time_saved, .. } => {
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

    /// The picker used to receive a single scalar; it must still understand it.
    #[test]
    fn vu_message_tolerates_a_scalar_only_producer() {
        match serde_json::from_str::<Wire>(r#"{"t":"vu","level":0.42}"#)
            .expect("scalar vu must parse")
        {
            Wire::Vu { level, levels } => {
                assert_eq!(level, 0.42);
                assert!(levels.is_empty(), "no per-device levels were supplied");
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
                    vec![
                        VuLevel { i: 4, level: 0.0 },
                        VuLevel { i: 5, level: 0.44 },
                    ]
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
        let line = command_line("set_device", serde_json::json!({ "device": "Mic \"Pro\" \\2" }));
        let parsed: serde_json::Value = serde_json::from_str(&line).expect("must stay valid JSON");
        assert_eq!(parsed["t"], "cmd");
        assert_eq!(parsed["cmd"], "set_device");
        assert_eq!(parsed["device"], "Mic \"Pro\" \\2");
    }

    /// Mirror of `send_cmd_json` that returns the line instead of writing it.
    fn command_line(cmd: &str, extra: serde_json::Value) -> String {
        let mut obj = serde_json::Map::new();
        obj.insert("t".to_string(), serde_json::Value::String("cmd".to_string()));
        obj.insert("cmd".to_string(), serde_json::Value::String(cmd.to_string()));
        if let serde_json::Value::Object(extra) = extra {
            obj.extend(extra);
        }
        serde_json::Value::Object(obj).to_string()
    }

    #[test]
    fn tx_message_tolerates_only_the_required_text_field() {
        /// Every numeric field is `#[serde(default)]`, so a minimal producer works.
        let json = r#"{"t":"tx","text":"minimal"}"#;
        match serde_json::from_str::<Wire>(json).expect("minimal payload must parse") {
            Wire::Tx { lifetime_time_saved, time_saved, session_time_saved, .. } => {
                assert_eq!(lifetime_time_saved, None);
                assert_eq!(time_saved, None);
                assert_eq!(session_time_saved, None);
            }
            other => panic!("expected Tx, got {other:?}"),
        }
    }
}

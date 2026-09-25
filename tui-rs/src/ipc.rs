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
    pub channels: usize,
    #[serde(default)]
    pub is_default: bool,
    #[serde(default)]
    pub is_active: bool,
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
    Vu { level: f32 },
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
        middle_click_enabled: Option<bool>,
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
    let line = format!("{{\"t\":\"cmd\",\"cmd\":\"{cmd}\",\"{key}\":\"{val}\"}}\n");
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

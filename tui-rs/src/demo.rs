//! A scripted "fake engine" so the prototype can be evaluated without the
//! Python audio/ASR pipeline. It emits the same `Action`s the real engine would.

use std::time::{Duration, Instant};

use crate::app::{Action, Level, OutStatus, RunState, Theme};

pub struct Script {
    steps: Vec<(Duration, Action)>,
    started: Instant,
    idx: usize,
    pub paused: bool,
}

impl Script {
    pub fn new() -> Self {
        use Action::*;
        let s = |secs: f32| Duration::from_secs_f32(secs);

        let steps: Vec<(Duration, Action)> = vec![
            (
                s(0.6),
                Event {
                    title: "SYSTEM READY".into(),
                    message: "Warming up model weights and audio device.".into(),
                    level: Level::Info,
                },
            ),
            (
                s(1.4),
                State(RunState::Recording, String::new()),
            ),
            (
                s(4.6),
                State(
                    RunState::Processing,
                    "Transcribing stream with Cohere...".into(),
                ),
            ),
            (
                s(5.9),
                Transcribe {
                    text: "This is the ratatui prototype of the voice transcriber status line, streaming cleanly into scrollback.".into(),
                    rec: 3.2,
                    proc: 1.35,
                    ready: 0.62,
                    status: OutStatus::Typed,
                },
            ),
            (s(8.0), State(RunState::Recording, String::new())),
            (
                s(10.1),
                State(RunState::Processing, "Transcribing stream with Cohere...".into()),
            ),
            (
                s(11.3),
                State(RunState::Rewriting, String::new()),
            ),
            (
                s(12.4),
                Transcribe {
                    text: "Remind me to ship the ratatui migration next Tuesday at three thirty.".into(),
                    rec: 2.1,
                    proc: 1.05,
                    ready: 0.48,
                    status: OutStatus::Typed,
                },
            ),
            (
                s(14.0),
                Event {
                    title: "THEME SWITCHED".into(),
                    message: "UI Color Theme set to 'cyan' (Active: CYAN)".into(),
                    level: Level::Info,
                },
            ),
            (s(14.0), SetTheme(Theme::Cyan)),
            (s(15.2), SetMute(true)),
            (s(15.6), State(RunState::Recording, String::new())),
            (
                s(17.6),
                State(RunState::Processing, "Transcribing stream with Cohere...".into()),
            ),
            (
                s(18.9),
                Transcribe {
                    text: "No wait, make that Wednesday.".into(),
                    rec: 2.0,
                    proc: 0.92,
                    ready: 0.41,
                    status: OutStatus::Copied,
                },
            ),
            (
                s(20.0),
                Event {
                    title: "TUNING".into(),
                    message: "Dictionary hit: 'ratatui' → 'Ratatui'. Numbers conversion enabled.".into(),
                    level: Level::Warning,
                },
            ),
            (s(21.0), SetMute(false)),
            (s(21.0), SetTheme(Theme::Auto)),
            (s(21.4), State(RunState::Ready, String::new())),
        ];

        Self {
            steps,
            started: Instant::now(),
            idx: 0,
            paused: false,
        }
    }

    /// Return all actions that became due since the last tick. Loops forever.
    pub fn tick(&mut self) -> Vec<Action> {
        if self.paused {
            return Vec::new();
        }
        let elapsed = self.started.elapsed();
        let mut out = Vec::new();
        while self.idx < self.steps.len() && self.steps[self.idx].0 <= elapsed {
            out.push(self.steps[self.idx].1.clone());
            self.idx += 1;
        }
        if self.idx >= self.steps.len() {
            self.started = Instant::now();
            self.idx = 0;
        }
        out
    }

    pub fn pause(&mut self) {
        self.paused = true;
    }
}

#!/usr/bin/env python3
"""
The optional on-device formatter's settings, end to end (milestone C5).

The load-bearing test in this file is
:func:`test_formatter_off_is_byte_identical`: the feature ships **off**, and off
has to mean the dictation path is indistinguishable from a build without it. Every
other test here is about the feature working; that one is about it not existing.

Model-free throughout. The formatter backend is stubbed, so nothing here needs
the 462 MiB GGUF and nothing reaches the network.
"""
import sys
import types
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2  # noqa: E402
import post_processor as pp  # noqa: E402
import formatter  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_SETTINGS_PICKER = REPO_ROOT / "tui-rs" / "src" / "settings_picker.rs"

#: A transcript with fillers, a self-correction and a spoken list, so the
#: formatter has something real to change if it runs.
SAMPLE = "um so I need to buy milk and eggs, actually no, bread"

#: What the stub formatter returns when it is asked to rewrite.
REWRITTEN = "I need to buy bread."


def _stub(text, **kwargs):
    _stub.calls.append((text, kwargs))
    return REWRITTEN


_stub.calls = []


@pytest.fixture
def backend():
    """Install a model-free formatter backend and record how it was called."""
    _stub.calls = []
    module = types.SimpleNamespace(
        available=lambda: True, warm=lambda: None, format_text=_stub)
    saved = dict(formatter.BACKENDS), dict(formatter._loaded)
    saved_model = t2.FORMATTER_MODEL
    formatter.BACKENDS["stub"] = "voice_transcriber.formatters.stub"
    formatter._loaded["stub"] = module
    # Point the setting at the stub, or the run would resolve the default
    # backend, find no module and fall open - which would make every assertion
    # below pass for the wrong reason.
    t2.FORMATTER_MODEL = "stub"
    yield _stub
    t2.FORMATTER_MODEL = saved_model
    formatter.BACKENDS.clear()
    formatter.BACKENDS.update(saved[0])
    formatter._loaded.clear()
    formatter._loaded.update(saved[1])


@pytest.fixture(autouse=True)
def _restore_settings():
    """Leave every global this file touches exactly as it was found.

    Snapshotting DEFAULT_SETTINGS keys matters more than it looks: one test
    below calls reset_to_defaults(), which rewrites every setting global, and
    the post-processor keeps its own copies of punctuation/structure/cleanup.
    Restoring only the four formatter keys leaked a changed punctuation preset
    into every later test in the session.
    """
    keys = list(t2.DEFAULT_SETTINGS) + ["PUNCTUATION_MODE", "NUMBER_DIGITS"]
    saved = {key: getattr(t2, key, None) for key in keys}
    saved_pp = (
        pp.get_formatter_settings(), pp.get_cleanup_mode(),
        pp.get_punctuation_mode(), pp.get_structure_mode(),
        pp.get_number_digits_mode(), pp.is_number_digits_enabled(),
    )
    yield
    for key, value in saved.items():
        if value is not None:
            setattr(t2, key, value)
    pp.set_formatter_settings(**saved_pp[0])
    pp.set_cleanup_mode(saved_pp[1])
    pp.set_punctuation_mode(saved_pp[2])
    pp.set_structure_mode(saved_pp[3])
    pp.set_number_digits_mode(saved_pp[4])
    pp.set_number_digits_enabled(saved_pp[5])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point config persistence at a throwaway file and clear the env overrides."""
    import yaml
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, "CONFIG_FILE", path)
    monkeypatch.setattr(t2, "get_config_file", lambda: path)
    for var in ("VT_FORMATTER", "VT_FORMATTER_MODEL", "VT_FORMATTER_STYLE",
                "VT_FORMATTER_CONTEXT"):
        monkeypatch.delenv(var, raising=False)
    return path


def _run(text=SAMPLE, **kwargs):
    t2._push_formatter_settings()
    return pp.clean_speech_transcription(text, skip_slm=True, **kwargs)


# ---------------------------------------------------------------------------
# The headline regression: off must not exist
# ---------------------------------------------------------------------------
def test_formatter_off_is_byte_identical(backend, cfg):
    """With the formatter off the stage is a true no-op.

    Three claims, and the middle one is what stops this being vacuous: a test
    that only asserted "nothing happened" would also pass if the wiring were
    broken and the formatter could never run at all.
    """
    t2.set_formatter("off")
    off = _run()

    # 1. The backend is never consulted.
    assert backend.calls == [], "the backend ran while the formatter was off"

    # 2. The very same input *does* change when it is on, so (1) is meaningful.
    t2.set_formatter("on")
    on = _run()
    assert backend.calls, "the stub never ran, so 'off' proves nothing"
    assert on == REWRITTEN
    assert off != on

    # 3. Off is the shipped default, so what was measured above is stock behaviour,
    #    and the cleaned text survives untouched rather than being rewritten.
    assert t2.DEFAULT_SETTINGS["FORMATTER"] == "off"
    assert "milk and eggs" in off
    assert pp.get_formatter_settings() == {
        "enabled": True, "model": "stub", "style": "semi-formal",
        "context": "general"}


def test_the_default_is_off():
    assert t2.DEFAULT_SETTINGS["FORMATTER"] == "off"
    assert t2.DEFAULT_SETTINGS["FORMATTER_STYLE"] == "semi-formal"
    assert t2.DEFAULT_SETTINGS["FORMATTER_CONTEXT"] == "general"
    assert t2.DEFAULT_SETTINGS["FORMATTER_MODEL"] == "s1-mini"


# ---------------------------------------------------------------------------
# The cleanup gate
# ---------------------------------------------------------------------------
def test_cleanup_off_disables_the_formatter(backend, cfg):
    """`cleanup_mode: off` promises nothing is deleted; the formatter deletes."""
    t2.set_formatter("on")
    t2.set_cleanup_mode("off")
    off = _run()
    assert backend.calls == [], "the formatter ran with cleanup off"
    assert off == pp.clean_speech_transcription(SAMPLE, skip_slm=True)

    t2.set_cleanup_mode("full")
    assert _run() == REWRITTEN


def test_effective_formatter_reflects_the_cleanup_gate():
    t2.set_formatter("on")
    t2.set_cleanup_mode("full")
    assert t2.get_effective_formatter() == "on"
    t2.set_cleanup_mode("off")
    assert t2.get_formatter() == "on", "the configured value is unchanged"
    assert t2.get_effective_formatter() == "off", "but it cannot run"


# ---------------------------------------------------------------------------
# Fail open
# ---------------------------------------------------------------------------
def test_no_backend_means_the_text_passes_through(cfg):
    t2.set_formatter("on")
    t2.FORMATTER_MODEL = "s1-mini"  # registered, but its module is not installed
    assert _run() == pp.clean_speech_transcription(SAMPLE, skip_slm=True)


def test_a_raising_backend_leaves_the_text_alone(cfg):
    module = types.SimpleNamespace(
        available=lambda: True, warm=lambda: None,
        format_text=lambda text, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    formatter.BACKENDS["boom"] = "voice_transcriber.formatters.boom"
    formatter._loaded["boom"] = module
    try:
        t2.set_formatter("on")
        t2.FORMATTER_MODEL = "boom"
        assert _run() == pp.clean_speech_transcription(SAMPLE, skip_slm=True)
    finally:
        formatter.BACKENDS.pop("boom", None)
        formatter._loaded.pop("boom", None)


# ---------------------------------------------------------------------------
# The settings contract
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("on", "on"), ("ON", "on"), (" true ", "on"), ("yes", "on"), ("1", "on"),
    ("off", "off"), ("garbage", "off"), (None, "off"), ("", "off"),
])
def test_unknown_enabled_values_mean_off(value, expected):
    """The opposite of cleanup_mode, and deliberate: off is the safe default."""
    assert t2.normalize_formatter_enabled(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("casual", "casual"), ("semi_casual", "semi-casual"), ("FORMAL", "formal"),
    ("garbage", "semi-formal"), (None, "semi-formal"),
])
def test_style_normalisation(value, expected):
    assert t2.normalize_formatter_style(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("email", "email"), ("EMAIL", "email"),
    ("garbage", "general"), (None, "general"),
])
def test_context_normalisation(value, expected):
    assert t2.normalize_formatter_context(value) == expected


def test_model_normalisation_falls_back_to_the_shipped_default():
    assert t2.normalize_formatter_model("") == "s1-mini"
    assert t2.normalize_formatter_model(None) == "s1-mini"
    assert t2.normalize_formatter_model("llama-server") == "llama-server"


def test_setting_names_are_the_documented_ones(cfg):
    """The config keys, env vars and defaults must match the plan and the docs."""
    assert set(t2.DEFAULT_SETTINGS) >= {
        "FORMATTER", "FORMATTER_MODEL", "FORMATTER_STYLE", "FORMATTER_CONTEXT"}
    t2.load_audio_config()
    assert (t2.get_formatter(), t2.get_formatter_model(),
            t2.get_formatter_style(), t2.get_formatter_context()) == (
        "off", "s1-mini", "semi-formal", "general")


# ---------------------------------------------------------------------------
# Persistence and environment
# ---------------------------------------------------------------------------
def test_settings_round_trip_through_the_config_file(cfg):
    import yaml
    t2.set_formatter("on")
    t2.set_formatter_model("llama-server")
    t2.set_formatter_style("formal")
    t2.set_formatter_context("email")

    saved = yaml.safe_load(cfg.read_text())
    assert saved["formatter"] == "on"
    assert saved["formatter_model"] == "llama-server"
    assert saved["formatter_style"] == "formal"
    assert saved["formatter_context"] == "email"

    t2.FORMATTER = "off"
    t2.FORMATTER_STYLE = "semi-formal"
    t2.load_audio_config()
    assert (t2.get_formatter(), t2.get_formatter_model(),
            t2.get_formatter_style(), t2.get_formatter_context()) == (
        "on", "llama-server", "formal", "email")


def test_env_overrides_beat_the_config_file(cfg, monkeypatch):
    import yaml
    cfg.write_text(yaml.dump({"formatter": "off", "formatter_style": "casual"}))
    monkeypatch.setenv("VT_FORMATTER", "on")
    monkeypatch.setenv("VT_FORMATTER_STYLE", "formal")
    monkeypatch.setenv("VT_FORMATTER_CONTEXT", "email")
    monkeypatch.setenv("VT_FORMATTER_MODEL", "llama-server")
    t2.load_audio_config()
    assert (t2.get_formatter(), t2.get_formatter_style(),
            t2.get_formatter_context(), t2.get_formatter_model()) == (
        "on", "formal", "email", "llama-server")


def test_reset_restores_the_shipped_defaults(cfg, backend):
    t2.set_formatter("on")
    t2.set_formatter_style("formal")
    t2.reset_to_defaults()
    assert t2.get_formatter() == "off"
    assert t2.get_formatter_style() == "semi-formal"
    assert t2.get_formatter_context() == "general"
    assert pp.get_formatter_settings()["enabled"] is False


# ---------------------------------------------------------------------------
# Cycling
# ---------------------------------------------------------------------------
def test_cycles_wrap(cfg):
    t2.set_formatter("off")
    assert t2.toggle_formatter() == "on"
    assert t2.toggle_formatter() == "off"

    t2.set_formatter_model("s1-mini")
    assert t2.cycle_formatter_model() == "llama-server"
    assert t2.cycle_formatter_model() == "s1-mini"

    t2.set_formatter_style("casual")
    assert [t2.cycle_formatter_style() for _ in range(4)] == [
        "semi-casual", "semi-formal", "formal", "casual"]

    t2.set_formatter_context("general")
    assert t2.cycle_formatter_context() == "email"
    assert t2.cycle_formatter_context() == "general"


def test_choices_are_the_registry_not_a_copy():
    """The backend list is the swap point, so it must not drift from the registry.

    `noop` is the one deliberate omission: it is the identity backend tests use,
    not something to offer a user.
    """
    assert set(t2.FORMATTER_MODELS) <= set(formatter.available_backends())
    assert set(formatter.available_backends()) - set(t2.FORMATTER_MODELS) == {"noop"}
    assert t2.FORMATTER_STYLES == list(formatter.STYLES)
    assert t2.FORMATTER_CONTEXTS == list(formatter.CONTEXTS)


# ---------------------------------------------------------------------------
# Settings labels (mirrored in the ratatui modal)
# ---------------------------------------------------------------------------
def test_formatter_labels():
    assert t2.formatter_setting_state("off") == (
        "Off: deterministic cleanup only", "[OFF]", "dim white")
    assert t2.formatter_setting_state("on") == (
        "Rewrites the transcript on this machine", "[ON]", "green")
    assert t2.formatter_model_setting_state("s1-mini") == (
        "Backend: s1-mini", "[MODEL]", "cyan")
    assert t2.formatter_style_setting_state("formal") == (
        "Writing style: formal", "[STYLE]", "cyan")
    assert t2.formatter_context_setting_state("email") == (
        "Context: email", "[CONTEXT]", "cyan")


def test_ratatui_formatter_labels_match_python():
    """The ratatui modal renders these strings from another language.

    Scoped to the non-test half of the file, because the Rust test repeats the
    same literals and matching those would let the real implementation drift.
    Mirrors the structure guard in tests/shared/test_config_sync.py.

    Two of the rows interpolate their value (``Backend: {value}``), so for those
    the *prefix* is what can be matched - the Rust source holds the format
    string, never a rendered example.
    """
    source = RUST_SETTINGS_PICKER.read_text(encoding="utf-8")
    implementation = source.split("\n#[cfg(test)]", 1)[0]
    assert "SettingKind::Formatter" in implementation, (
        "could not find the formatter rows in tui-rs/src/settings_picker.rs - "
        "the parity guard is looking in the wrong place")

    # Literal labels: must appear exactly.
    for label in ("Off: deterministic cleanup only",
                  "Rewrites the transcript on this machine"):
        assert label in implementation, f"ratatui no longer shows {label!r}"
    # Interpolated labels: match the prefix the format string carries.
    for prefix in ("Backend: ", "Writing style: ", "Context: "):
        assert prefix in implementation, f"ratatui no longer shows {prefix!r}"
    # Every badge, taken from Python so the two cannot disagree about them.
    for state in (t2.formatter_setting_state("off"), t2.formatter_setting_state("on"),
                  t2.formatter_model_setting_state("s1-mini"),
                  t2.formatter_style_setting_state("semi-formal"),
                  t2.formatter_context_setting_state("general")):
        assert state[1] in implementation, f"ratatui no longer shows {state[1]!r}"

    for command in ("cycle_formatter", "cycle_formatter_model",
                    "cycle_formatter_style", "cycle_formatter_context"):
        assert command in implementation, f"ratatui no longer sends {command}"


# ---------------------------------------------------------------------------
# The control API is the catalogue
# ---------------------------------------------------------------------------
def test_control_api_exposes_every_setting():
    """No behaviour may be reachable only by editing config."""
    import control
    for verb in ("formatter", "formatter-model", "formatter-style", "formatter-context"):
        assert verb in control.VERBS, f"{verb} is missing from the catalogue"
        assert "summary" in control.VERBS[verb]
    assert control.VERBS["formatter"]["choices"] == list(t2.FORMATTER_MODES)
    assert control.VERBS["formatter-style"]["choices"] == list(t2.FORMATTER_STYLES)
    assert control.VERBS["formatter-context"]["choices"] == list(t2.FORMATTER_CONTEXTS)


def test_status_advertises_configured_and_effective():
    """`status` promises both, so a `formatter: on` that cannot run is visible."""
    import control
    returned = control.VERBS["status"]["returns"]
    assert "formatter" in returned
    assert "formatter_setting" in returned
    assert "formatter_model" in returned

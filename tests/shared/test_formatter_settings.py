#!/usr/bin/env python3
"""
The optional on-device formatter's settings, end to end (milestone C5).

The load-bearing test in this file is
:func:`test_formatter_off_is_byte_identical`: the feature ships **off**, and off
has to mean the dictation path is indistinguishable from a build without it. Every
other test here is about the feature working; that one is about it not existing.

Model-free throughout. Every test that runs text through the formatter installs
its own backend (``backend`` or ``unavailable_backend``) rather than trusting
that the shipped one is absent, so nothing here needs the 462 MiB GGUF, nothing
reaches the network, and nothing spawns a real ``llama-server`` - which
``tests/shared/conftest.py`` enforces as a hard failure. An earlier version of
``test_no_backend_means_the_text_passes_through`` relied on the module simply not
being installed, and on a machine with the weights it ran real inference and
leaked the server process.
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
def backend(monkeypatch):
    """Install a model-free formatter backend and record how it was called.

    Patches **both** axes: the registry entry, and ``formatter.DEFAULT_BACKEND``.
    ``formatter_model`` names a *model*, not a runtime, so pointing the setting at
    a stub no longer reaches the stub - without the second patch the run would
    resolve the real default backend and every assertion below would pass or fail
    for the wrong reason.
    """
    _stub.calls = []
    module = types.SimpleNamespace(
        available=lambda model=None: True, warm=lambda: None, format_text=_stub)
    saved = dict(formatter.BACKENDS), dict(formatter._loaded)
    saved_model = t2.FORMATTER_MODEL
    formatter.BACKENDS["stub"] = "voice_transcriber.formatters.stub"
    formatter._loaded["stub"] = module
    monkeypatch.setattr(formatter, "DEFAULT_BACKEND", "stub")
    t2.FORMATTER_MODEL = formatter.DEFAULT_MODEL
    yield _stub
    t2.FORMATTER_MODEL = saved_model
    formatter.BACKENDS.clear()
    formatter.BACKENDS.update(saved[0])
    formatter._loaded.clear()
    formatter._loaded.update(saved[1])


def _unavailable(text, **kwargs):
    _unavailable.calls.append((text, kwargs))
    return "SHOULD NOT RUN"


_unavailable.calls = []


@pytest.fixture
def unavailable_backend(monkeypatch):
    """Install a backend whose ``available()`` is False, by construction.

    Mirrors :func:`backend` but reports itself unavailable. Forcing that branch
    this way is the whole point: the test below used to rely on the backend
    *module* not being installed, which stopped being true when ``llama-server``
    shipped and the conventional-install-path fallback started finding a
    machine's weights. On such a machine it resolved the real backend, ran real
    inference on the 462 MiB model, and leaked a ``llama-server`` process - all
    inside the supposedly model-free shared tier.
    """
    _unavailable.calls = []
    module = types.SimpleNamespace(
        available=lambda model=None: False,
        warm=lambda: None,
        format_text=_unavailable,
    )
    saved = dict(formatter.BACKENDS), dict(formatter._loaded)
    formatter.BACKENDS["unavailable"] = "voice_transcriber.formatters.unavailable"
    formatter._loaded["unavailable"] = module
    monkeypatch.setattr(formatter, "DEFAULT_BACKEND", "unavailable")
    yield _unavailable
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
        "enabled": True, "model": "s1-mini", "style": "semi-formal",
        "context": "general"}


def test_the_default_is_off():
    assert t2.DEFAULT_SETTINGS["FORMATTER"] == "off"
    assert t2.DEFAULT_SETTINGS["FORMATTER_STYLE"] == "semi-formal"
    assert t2.DEFAULT_SETTINGS["FORMATTER_CONTEXT"] == "general"
    # A *model*, and the two axes are asserted separately on purpose: conflating
    # them is what left the default pointing at a runtime that did not exist.
    assert t2.DEFAULT_SETTINGS["FORMATTER_MODEL"] == "s1-mini"
    assert t2.DEFAULT_SETTINGS["FORMATTER_MODEL"] == formatter.DEFAULT_MODEL
    assert formatter.DEFAULT_BACKEND in formatter.BACKENDS
    assert t2.FORMATTER_MODELS == list(formatter.MODELS)
    assert t2.FORMATTER_DEFAULT_MODEL == formatter.DEFAULT_MODEL


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
def test_no_backend_means_the_text_passes_through(unavailable_backend, cfg):
    """An unavailable backend is a no-op, and is never consulted.

    The unavailable branch is forced by the ``unavailable_backend`` fixture
    rather than inferred from the machine (no installed weights, not in a spawn
    cooldown). Relying on the machine is how this test came to run the real
    model, and only silently pass when a preceding failed spawn happened to be
    holding the 60 s cooldown.
    """
    t2.set_formatter("on")
    assert _run() == pp.clean_speech_transcription(SAMPLE, skip_slm=True)
    assert unavailable_backend.calls == [], (
        "an unavailable backend must not be called")


def test_a_raising_backend_leaves_the_text_alone(cfg, monkeypatch):
    """A backend that raises mid-format falls back to the untouched text."""
    module = types.SimpleNamespace(
        available=lambda model=None: True, warm=lambda: None,
        format_text=lambda text, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(formatter.BACKENDS, "boom", "voice_transcriber.formatters.boom")
    monkeypatch.setitem(formatter._loaded, "boom", module)
    # The raising backend has to *be* the resolved backend. Setting only
    # ``formatter_model`` did not do that, so this test used to exercise the
    # shipping default backend and never test its own subject.
    monkeypatch.setattr(formatter, "DEFAULT_BACKEND", "boom")
    t2.set_formatter("on")
    t2.FORMATTER_MODEL = "boom"
    assert _run() == pp.clean_speech_transcription(SAMPLE, skip_slm=True)


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


def test_model_normalisation_validates_and_falls_back():
    """An unknown model id names the default and carries on.

    Unlike a structure mode, this setting says *what to load* - so a typo should
    fall back rather than silently switch the feature off, which would look like
    the formatter had broken rather than like a bad config value.
    """
    assert t2.normalize_formatter_model("") == "s1-mini"
    assert t2.normalize_formatter_model(None) == "s1-mini"
    assert t2.normalize_formatter_model("s1-mini") == "s1-mini"
    assert t2.normalize_formatter_model("  S1-MINI  ") == "s1-mini"
    # Unknown values are not accepted as-is, which is the whole point: before,
    # any non-empty string was taken at face value and resolved to nothing.
    assert t2.normalize_formatter_model("llama-server") == "s1-mini"
    assert t2.normalize_formatter_model("gpt-9") == "s1-mini"


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
    t2.set_formatter_model("s1-mini")
    t2.set_formatter_style("formal")
    t2.set_formatter_context("email")

    saved = yaml.safe_load(cfg.read_text())
    assert saved["formatter"] == "on"
    assert saved["formatter_model"] == "s1-mini"
    assert saved["formatter_style"] == "formal"
    assert saved["formatter_context"] == "email"

    t2.FORMATTER = "off"
    t2.FORMATTER_STYLE = "semi-formal"
    t2.load_audio_config()
    assert (t2.get_formatter(), t2.get_formatter_model(),
            t2.get_formatter_style(), t2.get_formatter_context()) == (
        "on", "s1-mini", "formal", "email")


def test_an_unknown_model_in_the_config_is_validated_not_kept(cfg):
    """A bad model id must not survive into the running config.

    It used to: any non-empty string was accepted, then resolved to no module at
    all, so the formatter silently did nothing. Falling back to the shipped model
    keeps a config typo from looking like a broken feature.
    """
    import yaml
    cfg.write_text(yaml.dump({"formatter_model": "gpt-9-turbo"}))
    t2.load_audio_config()
    assert t2.get_formatter_model() == "s1-mini"


def test_env_overrides_beat_the_config_file(cfg, monkeypatch):
    import yaml
    cfg.write_text(yaml.dump({"formatter": "off", "formatter_style": "casual"}))
    monkeypatch.setenv("VT_FORMATTER", "on")
    monkeypatch.setenv("VT_FORMATTER_STYLE", "formal")
    monkeypatch.setenv("VT_FORMATTER_CONTEXT", "email")
    monkeypatch.setenv("VT_FORMATTER_MODEL", "s1-mini")
    t2.load_audio_config()
    assert (t2.get_formatter(), t2.get_formatter_style(),
            t2.get_formatter_context(), t2.get_formatter_model()) == (
        "on", "formal", "email", "s1-mini")


def test_an_unknown_model_env_override_falls_back(cfg, monkeypatch):
    monkeypatch.setenv("VT_FORMATTER_MODEL", "not-a-model")
    t2.load_audio_config()
    assert t2.get_formatter_model() == "s1-mini"


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
    # Built as a cycle over a list even though the list has one entry, so adding a
    # second model is data rather than a refactor. Cycling a one-element list is
    # deliberately a no-op rather than a special case.
    assert t2.cycle_formatter_model() == "s1-mini"
    assert t2.cycle_formatter_model() == "s1-mini"

    t2.set_formatter_style("casual")
    assert [t2.cycle_formatter_style() for _ in range(4)] == [
        "semi-casual", "semi-formal", "formal", "casual"]

    t2.set_formatter_context("general")
    assert t2.cycle_formatter_context() == "email"
    assert t2.cycle_formatter_context() == "general"


def test_the_model_list_is_not_a_backend_list():
    """The two axes must not be conflated - that is what the split was for.

    `formatter_model` names a *model*; the runtime is `formatter.DEFAULT_BACKEND`.
    Before, the same value served both, which is how the default ended up
    pointing at a runtime that did not exist. These lists must not drift apart
    silently in either direction: no backend name may appear as a model, and no
    model may appear as a backend.
    """
    assert t2.FORMATTER_MODELS == list(formatter.MODELS)
    assert t2.FORMATTER_DEFAULT_MODEL == formatter.DEFAULT_MODEL
    assert set(t2.FORMATTER_MODELS).isdisjoint(set(formatter.available_backends()))
    # `noop` is the identity backend tests use, never a model to offer a user.
    assert "noop" not in t2.FORMATTER_MODELS
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
        "Model: s1-mini", "[MODEL]", "cyan")
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
    for prefix in ("Model: ", "Writing style: ", "Context: "):
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

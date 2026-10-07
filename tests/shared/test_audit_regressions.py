#!/usr/bin/env python3
"""Regression tests for bugs found in the pre-launch audit.

Each test here pins a *specific defect* that shipped and was found by turning on
a linter and reading the code — not by a test, because no test covered it. They
are grouped in one file so the failure modes are documented together. Every one
of these passed silently before the fix, so they are worth more than their line
count:

1. ``load_audio_config`` assigned to three module globals without declaring them
   ``global``, so ``language``, ``wait_for_model_on_startup`` and ``enable_slm``
   were written to throwaway locals and silently ignored.
2. ``reset_terminal`` used ``sys`` before a later ``import sys`` made it local,
   so it raised ``UnboundLocalError`` on every call and the enclosing
   ``except Exception`` hid it. The terminal/clipboard reset did nothing.
3. The tkinter notification overlay built a Python script by string
   interpolation and ran it with ``python -c``, so a quote in the notification
   text broke the script and crafted text injected code.
"""

import os
import sys

import pytest
import yaml


# ---------------------------------------------------------------------------
# 1. Config values must actually reach the module globals.
# ---------------------------------------------------------------------------
class TestConfigGlobalsReachTheModule:
    """`language`, `wait_for_model_on_startup` and `enable_slm` are globals."""

    @pytest.fixture
    def cfg(self, tmp_path, monkeypatch):
        import t2

        path = tmp_path / "config.yaml"
        # `load_audio_config()` resolves its own path; point it at the temp file
        # so the real user config is never read or written.
        monkeypatch.setattr(t2, "get_config_file", lambda: path)
        saved = (t2.LANGUAGE, t2.WAIT_FOR_MODEL_ON_STARTUP, t2.ENABLE_SLM)
        saved_env = {
            k: os.environ.get(k)
            for k in ("VT_LANGUAGE", "VT_ENABLE_SLM", "VT_WAIT_FOR_MODEL_ON_STARTUP")
        }
        yield path
        # These are process-wide globals, so a test that changes them must put
        # them back or it contaminates every later test in the same run.
        t2.LANGUAGE, t2.WAIT_FOR_MODEL_ON_STARTUP, t2.ENABLE_SLM = saved
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_language_from_config_reaches_the_global(self, cfg, monkeypatch):
        import t2

        monkeypatch.delenv("VT_LANGUAGE", raising=False)
        cfg.write_text(yaml.dump({"language": "fr"}))
        t2.load_audio_config()
        assert t2.LANGUAGE == "fr"

    def test_wait_for_model_from_config_reaches_the_global(self, cfg, monkeypatch):
        import t2

        monkeypatch.delenv("VT_WAIT_FOR_MODEL_ON_STARTUP", raising=False)
        cfg.write_text(yaml.dump({"wait_for_model_on_startup": False}))
        t2.load_audio_config()
        assert t2.WAIT_FOR_MODEL_ON_STARTUP is False

    def test_enable_slm_from_config_reaches_the_global(self, cfg, monkeypatch):
        import t2

        monkeypatch.delenv("VT_ENABLE_SLM", raising=False)
        cfg.write_text(yaml.dump({"enable_slm": True}))
        t2.load_audio_config()
        assert t2.ENABLE_SLM is True

    def test_loaded_language_is_not_clobbered_on_save(self, cfg, monkeypatch):
        """A save used to write the stale default back over the user's file."""
        import t2

        monkeypatch.delenv("VT_LANGUAGE", raising=False)
        cfg.write_text(yaml.dump({"language": "de"}))
        t2.load_audio_config()
        t2.save_audio_config(file_path=cfg)
        assert yaml.safe_load(cfg.read_text())["language"] == "de"

    def test_env_still_overrides_the_config_file(self, cfg, monkeypatch):
        import t2

        cfg.write_text(yaml.dump({"language": "fr"}))
        monkeypatch.setenv("VT_LANGUAGE", "ja")
        t2.load_audio_config()
        assert t2.LANGUAGE == "ja"

    def test_resolved_language_is_published_to_the_backend(self, cfg, monkeypatch):
        """The acoustic backend reads `VT_LANGUAGE`, not the global."""
        import t2

        monkeypatch.delenv("VT_LANGUAGE", raising=False)
        cfg.write_text(yaml.dump({"language": "es"}))
        t2.load_audio_config()
        assert os.environ["VT_LANGUAGE"] == "es"


# ---------------------------------------------------------------------------
# 2. reset_terminal must actually do its job.
# ---------------------------------------------------------------------------
class TestResetTerminal:
    """It used to raise `UnboundLocalError` and swallow it, doing nothing."""

    @pytest.mark.skipif(sys.platform == "win32", reason="reset_terminal is a POSIX path")
    def test_outside_the_tui_it_runs_reset(self, monkeypatch):
        import subprocess

        import t2

        monkeypatch.delenv("VT_TUI_SOCKET", raising=False)
        monkeypatch.delenv("VT_TUI_BIN", raising=False)
        # Never shell out or signal real processes from a test: `pkill wl-copy`
        # would disturb the developer's actual clipboard.
        ran = []
        monkeypatch.setattr(os, "system", lambda cmd: ran.append(cmd))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))

        t2.reset_terminal()

        assert "reset" in ran, "reset_terminal did not reach the reset command"

    def test_inside_the_tui_it_leaves_the_terminal_alone(self, monkeypatch):
        """The TUI owns raw mode, so `reset` would fight it — but the stuck
        clipboard processes are still reaped, which is the other half of the
        feature and must not regress."""
        import subprocess

        import t2

        monkeypatch.setenv("VT_TUI_SOCKET", "/tmp/vt-test.sock")
        ran = []
        monkeypatch.setattr(os, "system", lambda cmd: ran.append(cmd))
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))

        t2.reset_terminal()

        assert "reset" not in ran, "the TUI owns the terminal; do not reset it"
        if sys.platform == "win32":
            assert ran == [], "there is no wl-copy to reap on Windows"
        else:
            assert ran, "stuck clipboard processes should still be cleaned up"


# ---------------------------------------------------------------------------
# 3. The overlay script must treat notification text as data, never code.
# ---------------------------------------------------------------------------
class _FakePopen:
    """Records the argv instead of spawning a process."""

    last_args = None

    def __init__(self, args, **kwargs):
        _FakePopen.last_args = list(args)
        self.args = list(args)

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass

    def kill(self):
        pass


@pytest.fixture
def overlay(monkeypatch):
    import notifications

    # Keep the test hermetic: no display probing, no `which` subprocesses.
    monkeypatch.setattr(
        notifications.VisualNotification, "_detect_display_environment", lambda self: "none"
    )
    monkeypatch.setattr(
        notifications.VisualNotification, "_detect_available_tools", lambda self: []
    )
    monkeypatch.setattr(notifications.subprocess, "Popen", _FakePopen)
    _FakePopen.last_args = None
    return notifications


class TestOverlayScriptIsNotInjectable:
    HOSTILE = 'x"); import os; os.system("echo pwned"); ("'

    def test_hostile_text_still_compiles(self, overlay):
        """The payload used to close the string and break (or inject into) it."""
        notifier = overlay.VisualNotification(app_name='App "quoted"', enable_logging=False)
        notifier._create_tkinter_overlay(self.HOSTILE, "#ff4444", False)

        script = _FakePopen.last_args[2]
        compile(script, "<overlay>", "exec")  # SyntaxError if the payload escaped

    def test_payload_is_passed_as_a_literal_not_as_code(self, overlay):
        notifier = overlay.VisualNotification(app_name="App", enable_logging=False)
        notifier._create_tkinter_overlay(self.HOSTILE, "#ff4444", False)

        script = _FakePopen.last_args[2]
        # repr() of the payload appears verbatim, i.e. as data inside a literal.
        assert repr(self.HOSTILE) in script
        # And the dangerous call is inside that literal, not a statement.
        assert 'import os; os.system("echo pwned")' not in script.replace(repr(self.HOSTILE), "")

    def test_the_persistence_flag_is_a_literal(self, overlay):
        notifier = overlay.VisualNotification(app_name="App", enable_logging=False)
        notifier._create_tkinter_overlay("hi", "#ff4444", True)
        script = _FakePopen.last_args[2]
        compile(script, "<overlay>", "exec")
        assert "if True:" in script


class TestOverlayProcessListIsGuarded:
    def test_cleanup_empties_the_list(self, overlay):
        notifier = overlay.VisualNotification(app_name="App", enable_logging=False)
        notifier._create_tkinter_overlay("hi", "#ff4444", False)
        assert len(notifier.overlay_processes) == 1

        notifier._cleanup_overlays()

        assert notifier.overlay_processes == []

    def test_cleanup_is_safe_to_call_when_empty(self, overlay):
        notifier = overlay.VisualNotification(app_name="App", enable_logging=False)
        notifier._cleanup_overlays()
        assert notifier.overlay_processes == []

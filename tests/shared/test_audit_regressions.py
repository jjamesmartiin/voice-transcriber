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
import socket
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


# ---------------------------------------------------------------------------
# 4. TUI ratatui socket security & permissions
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "AF_UNIX") or not hasattr(os, "getuid"),
    reason="Ratatui socket is Unix-only",
)
class TestTuiSocketSecurity:
    """Socket must use isolated directory (mode 0700) when XDG_RUNTIME_DIR is unset
    and socket file itself must be chmod 0600."""

    def test_default_socket_path_with_xdg(self, monkeypatch, tmp_path):
        import tui_ratatui

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        tui_obj = tui_ratatui.RatatuiTui(binary="dummy")
        assert tui_obj.socket_path == os.path.join(str(tmp_path), f"vt-tui-{os.getuid()}.sock")

    def test_default_socket_path_without_xdg_creates_isolated_dir(self, monkeypatch, tmp_path):
        import tempfile
        import tui_ratatui

        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        tui_obj = tui_ratatui.RatatuiTui(binary="dummy")
        expected_dir = os.path.join(str(tmp_path), f"vt-{os.getuid()}")
        assert tui_obj.socket_path == os.path.join(expected_dir, f"vt-tui-{os.getuid()}.sock")
        assert os.path.isdir(expected_dir)
        mode = os.stat(expected_dir).st_mode & 0o777
        assert mode == 0o700

    def test_socket_permissions_chmod_0600(self, monkeypatch):
        import shutil
        import socket
        import subprocess
        import tempfile
        import tui_ratatui

        d = tempfile.mkdtemp(prefix="vtsock-")
        sock_path = os.path.join(d, "test.sock")
        tui_obj = tui_ratatui.RatatuiTui(binary="dummy", socket_path=sock_path)
        tui_obj.ui_theme = "cyan"

        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            if args and args[0] and args[0][0] == "dummy":
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(sock_path)
                fake_popen._client = client

                class DummyProc:
                    def poll(self):
                        return None

                    def kill(self):
                        pass

                    def wait(self, timeout=None):
                        pass

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        pass

                return DummyProc()
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        try:
            tui_obj.start()
            mode = os.stat(sock_path).st_mode & 0o777
            assert mode == 0o600
        finally:
            tui_obj.stop()
            if hasattr(fake_popen, "_client"):
                fake_popen._client.close()
            shutil.rmtree(d, ignore_errors=True)

    def test_start_unlinking_handles_oserror_gracefully(self, monkeypatch):
        import shutil
        import socket
        import subprocess
        import tempfile
        import tui_ratatui

        d = tempfile.mkdtemp(prefix="vtsock-")
        sock_path = os.path.join(d, "test_oserror.sock")
        tui_obj = tui_ratatui.RatatuiTui(binary="dummy", socket_path=sock_path)
        tui_obj.ui_theme = "cyan"

        real_unlink = os.unlink

        def mock_unlink(path):
            if path == sock_path:
                raise OSError("Simulated unlink failure")
            real_unlink(path)

        monkeypatch.setattr(os, "unlink", mock_unlink)

        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            if args and args[0] and args[0][0] == "dummy":
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(sock_path)
                fake_popen._client = client

                class DummyProc:
                    def poll(self):
                        return None

                    def kill(self):
                        pass

                    def wait(self, timeout=None):
                        pass

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        pass

                return DummyProc()
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        try:
            # Should not raise OSError
            tui_obj.start()
        finally:
            # Restore unlink before stop() cleans up
            monkeypatch.undo()
            tui_obj.stop()
            if hasattr(fake_popen, "_client"):
                fake_popen._client.close()
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Config resolution must not hijack files from CWD when outside repo
# ---------------------------------------------------------------------------
class TestConfigResolutionIsolation:
    """Installed builds outside a repo checkout must not hijack arbitrary CWD files."""

    def test_get_config_file_ignores_cwd_when_repo_root_is_none(self, monkeypatch, tmp_path):
        import t2
        from voice_transcriber import model_download

        foreign_dir = tmp_path / "foreign"
        foreign_dir.mkdir()
        dummy_json = foreign_dir / "config.json"
        dummy_json.write_text('{"hijacked": true}')

        dummy_yaml = foreign_dir / "config.yaml"
        dummy_yaml.write_text("hijacked: true")

        monkeypatch.chdir(foreign_dir)
        monkeypatch.setattr(model_download, "find_repo_root", lambda: None)

        config_path = t2.get_config_file()
        assert config_path != dummy_json.resolve()
        assert config_path != dummy_yaml.resolve()
        assert config_path == t2.GLOBAL_CONFIG_FILE

    def test_get_config_file_anchors_to_repo_root(self, monkeypatch, tmp_path):
        import t2
        from voice_transcriber import model_download

        repo_dir = tmp_path / "fake_repo"
        repo_config_dir = repo_dir / "config"
        repo_config_dir.mkdir(parents=True)
        repo_yaml = repo_config_dir / "config.yaml"
        repo_yaml.write_text("language: en")

        other_dir = tmp_path / "other"
        other_dir.mkdir()
        other_yaml = other_dir / "config.yaml"
        other_yaml.write_text("language: es")

        monkeypatch.chdir(other_dir)
        monkeypatch.setattr(model_download, "find_repo_root", lambda: str(repo_dir))

        config_path = t2.get_config_file()
        assert config_path == repo_yaml.resolve()
        assert config_path != other_yaml.resolve()

    def test_dictionary_does_not_pollute_cwd_when_repo_root_is_none(self, monkeypatch, tmp_path):
        from voice_transcriber import dictionary, model_download

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        monkeypatch.chdir(work_dir)
        monkeypatch.setattr(model_download, "find_repo_root", lambda: None)
        monkeypatch.setattr(model_download, "get_data_dir", lambda: str(tmp_path / "data"))

        common = dictionary.get_common_dictionary_path()
        local = dictionary.get_local_dictionary_path(create=True)

        assert not (work_dir / "config").exists(), "CWD config directory should not be created"
        assert str(common).startswith(str(tmp_path / "data" / "config"))
        assert str(local).startswith(str(tmp_path / "data" / "config"))

    def test_the_cohere_loader_does_not_search_cwd(self, monkeypatch, tmp_path):
        """A model directory is loaded with ``trust_remote_code=True``.

        Regression: ``load_model`` searched ``<cwd>/models/cohere`` and accepted
        any directory holding a ``model.safetensors``, so a ``config.json`` plus a
        ``modeling_*.py`` dropped next to the directory the user launches from was
        executed as that user - remote code with no trust boundary at all.
        """
        from voice_transcriber import transcribe_cohere

        work_dir = tmp_path / "foreign"
        work_dir.mkdir()
        monkeypatch.chdir(work_dir)

        dirs = transcribe_cohere._candidate_model_dirs()
        cwd = os.path.abspath(str(work_dir))
        assert dirs
        assert not any(os.path.abspath(d).startswith(cwd) for d in dirs), dirs


# ---------------------------------------------------------------------------
# 6. Zenity notifications must escape XML/Pango characters
# ---------------------------------------------------------------------------
class TestZenityNotificationEscaping:
    """Zenity Pango markup requires <>& to be escaped to prevent syntax errors."""

    def test_zenity_escapes_xml_characters(self, monkeypatch):
        import notifications

        monkeypatch.setattr(
            notifications.VisualNotification, "_detect_display_environment", lambda self: "none"
        )
        monkeypatch.setattr(
            notifications.VisualNotification, "_detect_available_tools", lambda self: []
        )

        commands = []

        def fake_popen(cmd, **kwargs):
            commands.append(cmd)

            class DummyProcess:
                pass

            return DummyProcess()

        monkeypatch.setattr(notifications.subprocess, "Popen", fake_popen)
        notifier = notifications.VisualNotification(app_name="TestApp", enable_logging=False)
        notifier._create_zenity_notification('Error: <50% & "High"> alert!', persistent=False)

        assert len(commands) == 1
        cmd = commands[0]
        text_arg = next(arg for arg in cmd if arg.startswith("--text="))
        assert '<span font="12">Error: &lt;50% &amp; &quot;High&quot;&gt; alert!</span>' in text_arg
        assert "<50%" not in text_arg
        assert "& " not in text_arg

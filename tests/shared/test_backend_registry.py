#!/usr/bin/env python3
"""The ASR backend registry.

`transcribe2.BACKENDS` is the single place a backend is declared. These tests pin
the properties that make that true — that the default is declared, that an unknown
name fails loudly and helpfully instead of silently keeping the old backend, and
that selecting a backend does **not** import it (which is what lets the config
loader call it without paying for `torch` at startup).

The real backend is never imported here: the Windows and macOS CI jobs do not
install torch, and `transcribe_cohere` imports it at module level. Where the real
module must be touched, `pytest.importorskip` is the established pattern (see
`test_crossplatform_runtime.py`).
"""

import os
import sys
import types

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

import transcribe2


@pytest.fixture
def fake_backend(monkeypatch):
    """A stand-in backend module, registered under a fake name.

    Injected into `sys.modules` so `import_module` finds it without touching the
    filesystem, and removed again afterwards.
    """
    module = types.ModuleType("vt_fake_backend")
    module.transcribe_audio = lambda **kwargs: {"text": "fake"}
    module.preload_model = lambda device="cpu": None
    monkeypatch.setitem(sys.modules, "vt_fake_backend", module)
    monkeypatch.setitem(transcribe2.BACKENDS, "fake", "vt_fake_backend")
    monkeypatch.setattr(transcribe2, "_loaded", {})
    yield module
    sys.modules.pop("vt_fake_backend", None)


class TestRegistry:
    def test_the_default_backend_is_declared(self):
        assert transcribe2.DEFAULT_BACKEND in transcribe2.BACKENDS

    def test_available_backends_lists_every_declaration(self):
        assert transcribe2.available_backends() == sorted(transcribe2.BACKENDS)

    def test_every_entry_is_an_import_path(self):
        for name, path in transcribe2.BACKENDS.items():
            assert isinstance(path, str) and path, name

    def test_required_functions_are_declared(self):
        assert transcribe2.REQUIRED_FUNCTIONS, "a backend with no contract is not checkable"

    def test_an_unknown_backend_error_is_a_value_error(self):
        """So a caller can catch it without importing the registry's own class."""
        assert issubclass(transcribe2.UnknownBackendError, ValueError)


class TestResolve:
    def test_case_and_whitespace_are_tolerated(self):
        assert transcribe2.resolve_backend_name("  CoHeRe ") == "cohere"

    def test_none_resolves_to_the_default(self):
        assert transcribe2.resolve_backend_name(None) == transcribe2.DEFAULT_BACKEND

    def test_an_unknown_name_lists_what_is_available(self):
        with pytest.raises(transcribe2.UnknownBackendError) as excinfo:
            transcribe2.resolve_backend_name("whisper")
        message = str(excinfo.value)
        assert "whisper" in message
        for name in transcribe2.available_backends():
            assert name in message


class TestLoad:
    def test_a_declared_backend_loads(self, fake_backend):
        module = transcribe2.get_backend("fake")
        assert module is fake_backend
        assert callable(module.transcribe_audio)

    def test_the_module_is_cached(self, fake_backend):
        assert transcribe2.get_backend("fake") is transcribe2.get_backend("fake")

    def test_a_backend_missing_its_contract_fails_loudly(self, monkeypatch):
        """A half-added backend must fail at startup, not at the first utterance."""
        monkeypatch.setitem(transcribe2.BACKENDS, "broken", "json")
        with pytest.raises(transcribe2.UnknownBackendError) as excinfo:
            transcribe2.get_backend("broken")
        message = str(excinfo.value)
        assert "broken" in message
        assert "transcribe_audio" in message

    def test_an_unimportable_backend_says_so(self, monkeypatch):
        monkeypatch.setitem(transcribe2.BACKENDS, "ghost", "voice_transcriber.nope")
        with pytest.raises(transcribe2.UnknownBackendError, match="could not be imported"):
            transcribe2.get_backend("ghost")

    def test_get_backend_makes_the_named_backend_active(self, fake_backend):
        transcribe2.get_backend("fake")
        assert transcribe2.get_backend_name() == "fake"


class TestSetBackend:
    def test_it_returns_the_resolved_name(self):
        assert transcribe2.set_backend("COHERE") == "cohere"

    def test_none_selects_the_default(self):
        assert transcribe2.set_backend(None) == transcribe2.DEFAULT_BACKEND

    def test_an_unknown_name_is_an_error_not_a_silent_no_op(self):
        """The old shim logged a warning and kept going; a typo must be visible."""
        with pytest.raises(transcribe2.UnknownBackendError):
            transcribe2.set_backend("definitely-not-a-backend")

    def test_it_does_not_import_the_backend(self, monkeypatch):
        """This is what makes it callable from the config loader.

        Importing `transcribe_cohere` pulls in torch and transformers; doing that
        while reading config would move a multi-second cost to startup.
        """
        monkeypatch.setattr(transcribe2, "_loaded", {})
        transcribe2.set_backend(transcribe2.DEFAULT_BACKEND)
        assert transcribe2._loaded == {}, "set_backend must not import the backend"

    def test_selecting_a_backend_does_not_disturb_the_name_on_failure(self, fake_backend):
        transcribe2.set_backend("fake")
        with pytest.raises(transcribe2.UnknownBackendError):
            transcribe2.set_backend("nope")
        # A rejected change must leave the previous choice in place.
        assert transcribe2.get_backend_name() == "fake"


class TestConfigIntegration:
    """`model_backend:` in the config file."""

    @pytest.fixture
    def cfg(self, tmp_path, monkeypatch):
        import t2

        path = tmp_path / "config.yaml"
        monkeypatch.setattr(t2, "get_config_file", lambda: path)
        saved_backend = t2.MODEL_BACKEND
        saved_active = transcribe2._active
        yield path
        t2.MODEL_BACKEND = saved_backend
        transcribe2._active = saved_active

    def test_a_valid_name_reaches_the_module(self, cfg):
        import t2

        cfg.write_text(yaml.dump({"model_backend": "cohere"}))
        t2.load_audio_config()
        assert t2.MODEL_BACKEND == "cohere"
        assert transcribe2.get_backend_name() == "cohere"

    def test_an_invalid_name_warns_and_falls_back(self, cfg, caplog):
        import t2

        cfg.write_text(yaml.dump({"model_backend": "whisper"}))
        with caplog.at_level("WARNING"):
            t2.load_audio_config()

        assert t2.MODEL_BACKEND == transcribe2.DEFAULT_BACKEND
        assert "invalid model_backend" in caplog.text
        # The warning must name what is actually in force, not just the error.
        assert transcribe2.DEFAULT_BACKEND in caplog.text

    def test_the_choice_is_persisted(self, cfg):
        import t2

        cfg.write_text(yaml.dump({"model_backend": "cohere"}))
        t2.load_audio_config()
        t2.save_audio_config(file_path=cfg)
        assert yaml.safe_load(cfg.read_text())["model_backend"] == "cohere"

    def test_an_absent_key_uses_the_default(self, cfg):
        import t2

        cfg.write_text(yaml.dump({}))
        t2.load_audio_config()
        assert t2.MODEL_BACKEND == transcribe2.DEFAULT_BACKEND

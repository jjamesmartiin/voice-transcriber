"""Tests for transcribe_cohere.py - Cohere transcription backend."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, mock_open
import numpy as np

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture(autouse=True)
def reset_cohere_module():
    """Remove cached module so each test starts fresh."""
    for mod_name in list(sys.modules.keys()):
        if "transcribe_cohere" in mod_name:
            del sys.modules[mod_name]
    yield


@pytest.fixture
def mock_cohere_deps():
    """Set up all mocked dependencies for transcribe_cohere and return the module."""
    mock_torch = MagicMock()
    mock_torch.float16 = "float16"
    mock_torch.float32 = "float32"
    mock_torch.cuda.is_available.return_value = False

    mock_processor = MagicMock()
    mock_model_obj = MagicMock()
    mock_model_obj.transcribe.return_value = ["Hello from Cohere"]
    mock_model_obj.to.return_value = mock_model_obj

    mock_auto_processor = MagicMock()
    mock_auto_processor.from_pretrained.return_value = mock_processor

    mock_auto_model = MagicMock()
    mock_auto_model.from_pretrained.return_value = mock_model_obj

    mock_login = MagicMock()

    mock_transformers = MagicMock()
    mock_transformers.AutoProcessor = mock_auto_processor
    mock_transformers.AutoModelForSpeechSeq2Seq = mock_auto_model

    mock_hf_hub = MagicMock()
    mock_hf_hub.login = mock_login

    # Patch os.dup2/os.open/os.close ONLY during import to prevent stderr hijack,
    # then restore them so the rest of the test (including tmp_path) works normally.
    real_open = os.open
    real_dup = os.dup
    real_dup2 = os.dup2
    real_close = os.close

    with patch.dict(sys.modules, {
        "torch": mock_torch,
        "transformers": mock_transformers,
        "huggingface_hub": mock_hf_hub,
    }):
        with patch("os.open", return_value=99), patch("os.dup", return_value=88), \
             patch("os.dup2"), patch("os.close"):
            import transcribe_cohere

        # Restore real os functions after import
        os.open = real_open
        os.dup = real_dup
        os.dup2 = real_dup2
        os.close = real_close

        transcribe_cohere._model = None
        transcribe_cohere._processor = None
        yield (transcribe_cohere, mock_torch, mock_auto_processor,
               mock_auto_model, mock_model_obj, mock_processor, mock_login)


class TestGetToken:
    def test_env_var_token(self, mock_cohere_deps):
        tc = mock_cohere_deps[0]
        with patch.dict(os.environ, {"HF_TOKEN": "test_token_123"}):
            # Clear any file-based sources
            with patch("os.path.exists", return_value=False):
                token = tc.get_token()
                assert token == "test_token_123"

    def test_cwd_token_file(self, mock_cohere_deps, tmp_path):
        tc = mock_cohere_deps[0]
        token_file = tmp_path / "HF_TOKEN"
        token_file.write_text("file_token_456")
        os.environ.pop("HF_TOKEN", None)
        # Directly patch the _os reference used by the module
        original_getcwd = os.getcwd
        original_exists = os.path.exists
        tc._os.getcwd = lambda: str(tmp_path)
        tc._os.path.exists = original_exists
        tc._os.path.join = os.path.join
        tc._os.path.dirname = os.path.dirname
        tc._os.path.abspath = os.path.abspath
        tc._os.path.expanduser = os.path.expanduser
        tc._os.environ = os.environ
        try:
            token = tc.get_token()
            assert token == "file_token_456"
        finally:
            tc._os.getcwd = original_getcwd

    def test_no_token_returns_none(self, mock_cohere_deps):
        tc = mock_cohere_deps[0]
        with patch.dict(os.environ, {}, clear=False), \
             patch("os.path.exists", return_value=False):
            os.environ.pop("HF_TOKEN", None)
            token = tc.get_token()
            assert token is None


class TestCheckAuth:
    def test_check_auth_with_token(self, mock_cohere_deps, capsys):
        tc, _, _, _, _, _, mock_login = mock_cohere_deps
        with patch.object(tc, "get_token", return_value="test_token_abc"):
            result = tc.check_auth()
            assert result is True
            mock_login.assert_called_once()
            captured = capsys.readouterr()
            assert "Authentication detected" in captured.out

    def test_check_auth_without_token(self, mock_cohere_deps, capsys):
        tc = mock_cohere_deps[0]
        with patch.object(tc, "get_token", return_value=None):
            result = tc.check_auth()
            assert result is False
            captured = capsys.readouterr()
            assert "Authentication Info" in captured.out


class TestLoadModel:
    def test_load_from_cache(self, mock_cohere_deps, capsys):
        tc, _, mock_ap, mock_am, mock_model_obj, mock_proc, _ = mock_cohere_deps
        model, processor = tc.load_model()
        assert model is mock_model_obj
        assert processor is mock_proc
        # Should use local_files_only=True first
        first_call_kwargs = mock_ap.from_pretrained.call_args_list[0][1]
        assert first_call_kwargs.get("local_files_only") is True
        captured = capsys.readouterr()
        assert "Loading model from cache" in captured.out

    def test_load_downloads_on_cache_miss(self, mock_cohere_deps, capsys):
        tc, _, mock_ap, mock_am, mock_model_obj, mock_proc, _ = mock_cohere_deps
        # First call to processor (local_files_only) raises, second succeeds
        mock_ap.from_pretrained.side_effect = [Exception("Not cached"), mock_proc]
        # Model's from_pretrained is never reached on first try (processor fails first)
        # On second try it succeeds
        mock_am.from_pretrained.return_value = mock_model_obj
        with patch.object(tc, "check_auth", return_value=True), \
             patch.object(tc, "get_token", return_value="token"):
            model, processor = tc.load_model()
        assert model is mock_model_obj
        captured = capsys.readouterr()
        assert "not in cache" in captured.out.lower() or "Downloading" in captured.out


class TestGetModelSingleton:
    def test_singleton(self, mock_cohere_deps):
        tc = mock_cohere_deps[0]
        r1 = tc.get_model()
        r2 = tc.get_model()
        # Tuples are recreated each call, but the underlying model/processor objects are the same
        assert r1[0] is r2[0]
        assert r1[1] is r2[1]

    def test_prints_load_time(self, mock_cohere_deps, capsys):
        tc = mock_cohere_deps[0]
        tc.get_model()
        captured = capsys.readouterr()
        assert "Model loaded and ready in" in captured.out


class TestTranscribeAudio:
    def test_transcribe_with_audio_data(self, mock_cohere_deps, mock_audio_data, capsys):
        tc, _, _, _, mock_model_obj, _, _ = mock_cohere_deps
        result = tc.transcribe_audio(audio_data=mock_audio_data)
        assert result == "Hello from Cohere"
        captured = capsys.readouterr()
        assert "Transcription completed in" in captured.out
        assert "Hello from Cohere" in captured.out

    def test_transcribe_with_audio_path(self, mock_cohere_deps, capsys):
        tc, _, _, _, mock_model_obj, _, _ = mock_cohere_deps
        result = tc.transcribe_audio(audio_path="/tmp/test.wav")
        assert result == "Hello from Cohere"
        call_kwargs = mock_model_obj.transcribe.call_args[1]
        assert "audio_files" in call_kwargs

    def test_transcribe_error_handling(self, mock_cohere_deps, mock_audio_data, capsys):
        tc, _, _, _, mock_model_obj, _, _ = mock_cohere_deps
        mock_model_obj.transcribe.side_effect = RuntimeError("GPU OOM")
        result = tc.transcribe_audio(audio_data=mock_audio_data)
        assert result == ""
        captured = capsys.readouterr()
        assert "Transcription error" in captured.out

    def test_transcribe_model_load_error(self, mock_cohere_deps, mock_audio_data):
        tc = mock_cohere_deps[0]
        tc._model = None
        with patch.object(tc, "get_model", side_effect=Exception("Load failed")):
            result = tc.transcribe_audio(audio_data=mock_audio_data)
            assert "Error loading model" in result

    def test_transcribe_flattens_audio(self, mock_cohere_deps, capsys):
        tc, _, _, _, mock_model_obj, _, _ = mock_cohere_deps
        audio_2d = np.random.randn(16000, 1).astype(np.float32)
        tc.transcribe_audio(audio_data=audio_2d)
        call_kwargs = mock_model_obj.transcribe.call_args[1]
        arr = call_kwargs["audio_arrays"][0]
        assert arr.ndim == 1

    def test_transcribe_empty_result_no_text_printed(self, mock_cohere_deps, mock_audio_data, capsys):
        tc, _, _, _, mock_model_obj, _, _ = mock_cohere_deps
        mock_model_obj.transcribe.return_value = [""]
        tc.transcribe_audio(audio_data=mock_audio_data)
        captured = capsys.readouterr()
        assert "Transcription completed in" in captured.out


class TestUnloadModel:
    def test_unload(self, mock_cohere_deps, capsys):
        tc = mock_cohere_deps[0]
        tc.get_model()
        tc.unload_model()
        assert tc._model is None
        assert tc._processor is None
        captured = capsys.readouterr()
        assert "Unloading Cohere model" in captured.out

    def test_unload_when_no_model(self, mock_cohere_deps, capsys):
        tc = mock_cohere_deps[0]
        tc.unload_model()
        captured = capsys.readouterr()
        assert "Unloading" not in captured.out


class TestGetTokenEdgeCases:
    def test_root_dir_token_file(self, mock_cohere_deps, tmp_path):
        tc = mock_cohere_deps[0]
        os.environ.pop("HF_TOKEN", None)
        root_token = tmp_path / "HF_TOKEN"
        root_token.write_text("root_token_789")
        # Create subdir so getcwd returns a real path
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        orig_getcwd = tc._os.getcwd
        orig_abspath = tc._os.path.abspath
        original_exists = os.path.exists
        (tmp_path / "src").mkdir(exist_ok=True)
        tc._os.getcwd = lambda: str(subdir)
        tc._os.path.exists = original_exists
        tc._os.path.join = os.path.join
        tc._os.path.dirname = os.path.dirname
        tc._os.path.abspath = lambda f: str(tmp_path / "src" / "transcribe_cohere.py")
        tc._os.path.expanduser = os.path.expanduser
        tc._os.environ = os.environ
        try:
            token = tc.get_token()
            assert token == "root_token_789"
        finally:
            tc._os.getcwd = orig_getcwd
            tc._os.path.abspath = orig_abspath

    def test_huggingface_cache_token(self, mock_cohere_deps, tmp_path):
        tc = mock_cohere_deps[0]
        os.environ.pop("HF_TOKEN", None)
        cache_dir = tmp_path / ".cache" / "huggingface"
        cache_dir.mkdir(parents=True)
        (cache_dir / "token").write_text("cache_token_abc")

        # Create dirs so abspath doesn't confuse coverage
        (tmp_path / "src").mkdir(exist_ok=True)

        orig_getcwd = tc._os.getcwd
        orig_abspath = tc._os.path.abspath
        tc._os.getcwd = lambda: str(tmp_path)
        tc._os.path.exists = os.path.exists
        tc._os.path.join = os.path.join
        tc._os.path.dirname = os.path.dirname
        tc._os.path.abspath = lambda f: str(tmp_path / "src" / "transcribe_cohere.py")
        tc._os.path.expanduser = lambda p: str(tmp_path / p.lstrip("~/"))
        tc._os.environ = os.environ
        try:
            token = tc.get_token()
            assert token == "cache_token_abc"
        finally:
            tc._os.getcwd = orig_getcwd
            tc._os.path.abspath = orig_abspath


class TestLoadModelAccessDenied:
    def test_403_error(self, mock_cohere_deps, capsys):
        tc, _, mock_ap, mock_am, _, _, _ = mock_cohere_deps
        mock_ap.from_pretrained.side_effect = [
            Exception("Not cached"),
            MagicMock()
        ]
        mock_am.from_pretrained.side_effect = Exception("403 Forbidden access denied")
        with patch.object(tc, "check_auth", return_value=True), \
             patch.object(tc, "get_token", return_value="test_token"):
            with pytest.raises(Exception, match="403"):
                tc.load_model()
        captured = capsys.readouterr()
        assert "Access denied" in captured.out


class TestCheckAuthLoginError:
    def test_login_exception(self, mock_cohere_deps, capsys):
        tc, _, _, _, _, _, mock_login = mock_cohere_deps
        mock_login.side_effect = Exception("login failed")
        with patch.object(tc, "get_token", return_value="bad_token"):
            result = tc.check_auth()
            assert result is False
            captured = capsys.readouterr()
            assert "Error during Hugging Face login" in captured.out


class TestTranscribeNonListResult:
    def test_non_list_result(self, mock_cohere_deps, mock_audio_data, capsys):
        tc, _, _, _, mock_model_obj, _, _ = mock_cohere_deps
        mock_model_obj.transcribe.return_value = "single string result"
        result = tc.transcribe_audio(audio_data=mock_audio_data)
        assert result == "single string result"


class TestPreloadModel:
    def test_preload_runs(self, mock_cohere_deps):
        tc = mock_cohere_deps[0]
        thread = tc.preload_model()
        thread.join(timeout=5)
        assert not thread.is_alive()

    def test_preload_error(self, mock_cohere_deps, capsys):
        tc, _, mock_ap, _, _, _, _ = mock_cohere_deps
        tc._model = None
        mock_ap.from_pretrained.side_effect = Exception("warmup failed")
        thread = tc.preload_model()
        thread.join(timeout=5)
        captured = capsys.readouterr()
        assert "error" in captured.out.lower() or "Preload" in captured.out

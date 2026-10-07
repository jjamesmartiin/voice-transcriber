import os

# Configure OpenBLAS and MKL single-threading before NumPy/PyTorch are imported:
# a multithreaded OpenBLAS can abort in its static destructor while the
# interpreter is finalizing. These must be set before the first BLAS import to
# take effect, hence the placement above the remaining imports.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import sys
import time
import tempfile
import pytest
import numpy as np
import soundfile as sf
from pathlib import Path

# Add src directory to sys.path
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# NOTE: there is deliberately no pytest_unconfigure/pytest_sessionfinish that
# calls os._exit(). That hack was added to dodge a PyTorch/OpenBLAS static
# destructor abort during Py_FinalizeEx, but it skipped every finalizer, atexit
# handler and plugin teardown (hiding real failures). The single-threaded BLAS
# settings above, applied before the first import, are what actually keep the
# model-free tier exiting cleanly; verified over repeated full runs on Linux and
# with the real Cohere model loaded. tests/e2e is the only tier that loads the
# model.


# src/main.py configures logging the moment it is imported, and several test
# modules import it - which would create and write the developer's real per-user
# log (~/.local/share/vt/vt.log, %LOCALAPPDATA%\\vt\\vt.log on Windows) on every
# test run. VT_LOG_FILE wins over that default location, so point it at a temp
# file for the session. Tests that exercise the default path unset it for
# themselves (tests/shared/test_logging_setup.py).
_TEST_LOG_FILE = Path(tempfile.gettempdir()) / f"vt-tests-{os.getpid()}.log"
os.environ.setdefault("VT_LOG_FILE", str(_TEST_LOG_FILE))

# Lifetime dictation stats are persisted to <data dir>/stats.json. Tests must
# never touch the developer's real totals, so point VT_STATS_FILE at a temp file
# for the whole session. Tests that want a specific path monkeypatch the env var
# themselves (tests/shared/test_stats.py).
_TEST_STATS_FILE = Path(tempfile.gettempdir()) / f"vt-stats-tests-{os.getpid()}.json"
os.environ.setdefault("VT_STATS_FILE", str(_TEST_STATS_FILE))

class PowerCpuMonitor:
    """Utility class to measure CPU utilization and ACPI battery power consumption."""
    def __init__(self, sample_interval=0.05):
        import psutil
        self.process = psutil.Process(os.getpid())
        self.sample_interval = sample_interval

    def measure_cpu_percent(self, duration=1.0):
        """
        Measure process CPU utilization percentage over a specified duration in seconds.
        Returns average CPU percent across samples.
        """
        start_time = time.time()
        cpu_samples = []
        # Prime the psutil cpu_percent calculation
        self.process.cpu_percent(interval=None)

        while time.time() - start_time < duration:
            time.sleep(self.sample_interval)
            cpu_samples.append(self.process.cpu_percent(interval=None))

        return float(np.mean(cpu_samples)) if cpu_samples else 0.0

    def measure_system_power_watts(self):
        """
        Attempt to read power draw in Watts from Linux sysfs power_supply (e.g. laptop battery).
        Returns float Watts if available, or None if unavailable (e.g. AC desktop/VM).
        """
        power_supply_dir = Path("/sys/class/power_supply")
        if not power_supply_dir.exists():
            return None

        for bat in power_supply_dir.glob("BAT*"):
            power_now = bat / "power_now"
            current_now = bat / "current_now"
            voltage_now = bat / "voltage_now"

            if power_now.exists():
                try:
                    return float(power_now.read_text().strip()) / 1e6
                except Exception:
                    pass
            elif current_now.exists() and voltage_now.exists():
                try:
                    c = float(current_now.read_text().strip()) / 1e6  # Amps
                    v = float(voltage_now.read_text().strip()) / 1e6  # Volts
                    return c * v
                except Exception:
                    pass
        return None

@pytest.fixture
def cpu_power_monitor():
    return PowerCpuMonitor()


@pytest.fixture
def fake_asr(monkeypatch):
    """Replace the ASR backend with a deterministic stub (no model / no torch).

    Patches ``transcribe2.transcribe_audio``, which is the single seam used by
    both ``main.process_audio_stream`` and ``micro_batcher``'s worker. Tests can
    mutate ``state['text']`` to control the returned transcript.
    """
    import transcribe2

    state = {"text": "hello world", "calls": []}

    def _fake_transcribe_audio(audio_data=None, audio_path=None, sample_rate=16000, device="cpu", language="en"):
        n = 0 if audio_data is None else len(audio_data)
        state["calls"].append(n)
        return state["text"]

    monkeypatch.setattr(transcribe2, "transcribe_audio", _fake_transcribe_audio, raising=False)
    return state

@pytest.fixture(scope="session", autouse=True)
def cleanup_after_tests():
    """Unload ML models and collect garbage at session teardown."""
    yield
    try:
        import transcribe2
        transcribe2.unload_model()
    except Exception:
        pass
    import gc
    gc.collect()

_BENCHMARK_REPORTS = []


def record_benchmark(category: str, target: str, latency_str: str, throughput_str: str, accuracy_str: str, status: str = "PASS"):
    """Register a benchmark metric row to be reported in the pytest terminal summary."""
    _BENCHMARK_REPORTS.append({
        "category": category,
        "target": target,
        "latency": latency_str,
        "throughput": throughput_str,
        "accuracy": accuracy_str,
        "status": status,
    })


@pytest.fixture
def benchmark_reporter():
    return record_benchmark


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a clean latency & accuracy benchmark matrix table if any benchmarks ran."""
    if not _BENCHMARK_REPORTS:
        return

    tr = terminalreporter
    tr.write_sep("=", "⚡ LATENCY & ACCURACY BENCHMARK REPORT ⚡", bold=True, cyan=True)
    header = f"{'CATEGORY':<16} {'TARGET / SCENARIO':<26} {'LATENCY':<14} {'THROUGHPUT / RTF':<22} {'ACCURACY / STATUS'}"
    tr.write_line(header, bold=True)
    tr.write_line("-" * (len(header) + 4))

    for row in _BENCHMARK_REPORTS:
        status_color = {"green": True} if row["status"] == "PASS" else {"red": True}
        line = f"{row['category']:<16} {row['target']:<26} {row['latency']:<14} {row['throughput']:<22} {row['accuracy']}"
        tr.write_line(line, **status_color)
    tr.write_sep("=", "", cyan=True)


@pytest.fixture(scope="session")
def sample_audio_file(tmp_path_factory):
    """
    Provides a clean sample audio file with known ground truth text validation.
    Returns tuple: (audio_filepath, expected_text, sample_rate, duration_seconds).
    """
    tmp_dir = tmp_path_factory.mktemp("audio_data")
    wav_path = tmp_dir / "sample_speech.wav"
    expected_text = "the quick brown fox jumps over the lazy dog"

    # Try generating audio with gTTS if available
    try:
        from gtts import gTTS
        mp3_path = tmp_dir / "sample_speech.mp3"
        tts = gTTS(expected_text, lang="en")
        tts.save(str(mp3_path))

        # Convert to 16kHz mono WAV using soundfile / librosa or mpg123
        try:
            import librosa
            audio, sr = librosa.load(str(mp3_path), sr=16000)
            sf.write(str(wav_path), audio, 16000)
            duration = len(audio) / 16000
            return str(wav_path), expected_text, 16000, duration
        except Exception:
            pass
    except Exception:
        pass

    # Fallback synthetic clean 16kHz WAV audio signal
    sr = 16000
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration), False)
    # Multi-tone voice formant synthesis
    audio = (0.4 * np.sin(2 * np.pi * 300 * t) +
             0.3 * np.sin(2 * np.pi * 700 * t) +
             0.2 * np.sin(2 * np.pi * 2100 * t)).astype(np.float32)

    sf.write(str(wav_path), audio, sr)
    return str(wav_path), expected_text, sr, duration

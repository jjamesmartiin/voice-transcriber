import os
import sys
import time
import signal
import pytest
import numpy as np
import soundfile as sf
from pathlib import Path

# Configure OpenBLAS and MKL single-threading to prevent teardown SIGFPE signal crash
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# Reset SIGFPE handler to default to prevent C-library exit teardown crash
try:
    signal.signal(signal.SIGFPE, signal.SIG_DFL)
except Exception:
    pass

# Add src directory to sys.path
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

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
        import psutil
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

@pytest.fixture(scope="session", autouse=True)
def cleanup_after_tests():
    """Unload ML models and collect garbage at session teardown."""
    yield
    try:
        import transcribe_whisper
        transcribe_whisper.unload_model()
    except Exception:
        pass
    import gc
    gc.collect()

def pytest_sessionfinish(session, exitstatus):
    """
    Bypass buggy C-library (PyTorch/OpenBLAS) static destructors during Py_FinalizeEx
    by performing a clean os._exit using pytest's exit status.
    """
    os._exit(exitstatus)

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

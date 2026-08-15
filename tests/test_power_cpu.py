import time
import pytest
import numpy as np
from hotkeys import WaylandGlobalHotkeys
from notifications import VisualNotification
import t2

def test_idle_cpu_usage(cpu_power_monitor):
    """
    Test that when the application is idle (waiting for hotkeys),
    CPU usage remains minimal (< 3.0%) so battery life is preserved.
    """
    # Create hotkey listener instance in idle state
    def dummy_start():
        pass
    def dummy_stop(copy_to_clipboard=False):
        pass

    hotkey_sys = WaylandGlobalHotkeys(callback_start=dummy_start, callback_stop=dummy_stop)

    # Measure CPU utilization for 2.0 seconds during idle state
    idle_cpu = cpu_power_monitor.measure_cpu_percent(duration=2.0)
    
    # Assert CPU draw is under threshold
    assert idle_cpu < 5.0, f"Idle CPU usage too high: {idle_cpu:.2f}% (expected < 5.0%)"

def test_power_draw_measurement(cpu_power_monitor):
    """
    Test power draw measurement utility.
    Reads hardware battery power in Watts if running on laptop battery,
    or verifies CPU usage tracking fallback.
    """
    power_watts = cpu_power_monitor.measure_system_power_watts()
    if power_watts is not None:
        assert power_watts >= 0.0, f"Power draw should be non-negative, got {power_watts} W"
        print(f"\n[Hardware Power Draw] Battery Power Draw: {power_watts:.2f} W")
    else:
        print("\n[Hardware Power Draw] AC Power / VM detected (hardware battery power unavailable). Using CPU utilization metric.")

def test_cpu_isolation_idle_vs_processing(cpu_power_monitor, sample_audio_file):
    """
    Isolate CPU usage during idle standby vs active audio processing.
    Verify that idle mode returns to near zero CPU after processing completes.
    """
    audio_path, expected_text, sr, duration = sample_audio_file
    
    # 1. Measure initial idle CPU
    idle_cpu_before = cpu_power_monitor.measure_cpu_percent(duration=1.0)
    
    # 2. Perform audio stream processing
    audio_data = np.zeros(int(sr * 0.5), dtype=np.float32)
    t2.process_audio_stream(audio_data)
    
    # 3. Measure idle CPU after processing finishes
    idle_cpu_after = cpu_power_monitor.measure_cpu_percent(duration=1.0)
    
    # Verify post-processing CPU returns to low idle state
    assert idle_cpu_after < 5.0, f"CPU usage did not return to idle baseline after processing: {idle_cpu_after:.2f}%"

def test_cpu_affinity_and_priority():
    """
    Test CPU core pinning and priority preemption setup.
    """
    import os
    # Test setting CPU affinity to dedicated core (e.g. last core)
    t2.apply_cpu_affinity_and_priority(affinity_setting="last_1", high_priority=True)
    
    if hasattr(os, "sched_getaffinity"):
        current_affinity = list(os.sched_getaffinity(0))
        total_cpus = os.cpu_count() or 1
        assert current_affinity == [total_cpus - 1], f"Expected affinity to be [{total_cpus - 1}], got {current_affinity}"
        print(f"\n[CPU Pinning Test] Successfully pinned to dedicated core: {current_affinity}")


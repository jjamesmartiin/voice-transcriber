import os
import sys

# Auto-configure WSL audio passthrough if running inside WSL
if "PULSE_SERVER" not in os.environ:
    if os.path.exists("/mnt/wslg/runtime-dir/pulse/native"):
        os.environ["PULSE_SERVER"] = "unix:/mnt/wslg/runtime-dir/pulse/native"
    elif os.path.exists("/mnt/wslg/PulseServer"):
        os.environ["PULSE_SERVER"] = "/mnt/wslg/PulseServer"

import sounddevice as sd

print(f"[Audio Info] PULSE_SERVER={os.environ.get('PULSE_SERVER', 'Not set (using default driver)')}")
print("=" * 60)
print("Detected Audio Devices:")
print("=" * 60)
devices = sd.query_devices()
print(devices)
print("\n" + "=" * 60)
print("Input Capability Check (16000Hz, 1 Channel - Speech Recognition Standard):")
print("=" * 60)
for i, d in enumerate(devices):
    if d['max_input_channels'] > 0:
        try:
            sd.check_input_settings(device=i, samplerate=16000, channels=1)
            print(f"  [OK] Device {i} ({d['name']}): 16000Hz 1ch supported (Inputs: {d['max_input_channels']})")
        except Exception as e:
            print(f"  [FAIL] Device {i} ({d['name']}): NOT supported: {e}")


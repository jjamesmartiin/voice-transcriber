import sounddevice as sd

print(sd.query_devices())
for i, d in enumerate(sd.query_devices()):
    try:
        sd.check_input_settings(device=i, samplerate=16000, channels=1)
        print(f"Device {i} ({d['name']}): 16000Hz supported")
    except Exception as e:
        print(f"Device {i} ({d['name']}): 16000Hz NOT supported: {e}")

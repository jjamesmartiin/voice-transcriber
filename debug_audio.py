#!/usr/bin/env python
"""Debug script to list all available audio devices"""

import pyaudio
import sys

def list_audio_devices():
    """List all available audio devices with detailed info"""
    try:
        p = pyaudio.PyAudio()
        
        print("🎤 ALL AUDIO DEVICES:")
        print("=" * 80)
        
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
                print(f"Device {i}: {info['name']}")
                print(f"  Host API: {info['hostApi']}")
                print(f"  Max Input Channels: {info['maxInputChannels']}")
                print(f"  Max Output Channels: {info['maxOutputChannels']}")
                print(f"  Default Sample Rate: {info['defaultSampleRate']} Hz")
                print(f"  Default Low Input Latency: {info['defaultLowInputLatency']}")
                print(f"  Default High Input Latency: {info['defaultHighInputLatency']}")
                print()
            except Exception as e:
                print(f"Device {i}: Error getting info - {e}")
                print()
        
        print("🎤 INPUT DEVICES ONLY:")
        print("=" * 80)
        
        input_devices = []
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    input_devices.append((i, info))
                    print(f"Input Device {len(input_devices)-1}: (ID {i}) {info['name']}")
                    print(f"  Sample Rate: {info['defaultSampleRate']} Hz")
                    print(f"  Input Channels: {info['maxInputChannels']}")
                    print()
            except Exception as e:
                print(f"Device {i}: Error - {e}")
        
        p.terminate()
        
        if not input_devices:
            print("❌ No input devices found!")
            return False
        
        print(f"Found {len(input_devices)} input device(s)")
        return True
        
    except Exception as e:
        print(f"Error initializing PyAudio: {e}")
        return False

if __name__ == "__main__":
    list_audio_devices() 
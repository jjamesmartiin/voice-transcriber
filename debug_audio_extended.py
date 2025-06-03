#!/usr/bin/env python3
"""Extended debug script to find missing audio devices like Blue Snowball"""

import pyaudio
import sys
import os

def list_alsa_cards():
    """List ALSA cards from /proc/asound/cards"""
    print("🔍 ALSA CARDS FROM /proc/asound/cards:")
    print("=" * 60)
    try:
        with open('/proc/asound/cards', 'r') as f:
            content = f.read()
            print(content)
    except Exception as e:
        print(f"Error reading ALSA cards: {e}")
    print()

def test_specific_device(device_name):
    """Test a specific ALSA device"""
    print(f"🎯 Testing specific device: {device_name}")
    try:
        p = pyaudio.PyAudio()
        
        # Try to find device by name
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if device_name.lower() in info['name'].lower():
                print(f"  Found matching device {i}: {info['name']}")
                print(f"    Sample Rate: {info['defaultSampleRate']} Hz")
                print(f"    Input Channels: {info['maxInputChannels']}")
                return i
        
        print(f"  ❌ Device '{device_name}' not found in PyAudio")
        p.terminate()
        return None
        
    except Exception as e:
        print(f"  Error testing device: {e}")
        return None

def force_refresh_pyaudio():
    """Try to force PyAudio to refresh its device list"""
    print("🔄 Forcing PyAudio refresh...")
    try:
        # Terminate and reinitialize PyAudio
        for i in range(3):
            p = pyaudio.PyAudio()
            device_count = p.get_device_count()
            print(f"  Attempt {i+1}: Found {device_count} devices")
            p.terminate()
        
        return True
    except Exception as e:
        print(f"  Error refreshing PyAudio: {e}")
        return False

def test_hw_devices():
    """Test hardware devices directly"""
    print("🔧 Testing hardware devices directly:")
    print("=" * 60)
    
    # Test common ALSA device patterns
    hw_devices = [
        "hw:0,0",  # Arctis Nova 7
        "hw:1,0", 
        "hw:2,0",  # Generic audio
        "hw:3,0",  # Should be Blue Snowball
        "plughw:0,0",
        "plughw:3,0",
    ]
    
    p = pyaudio.PyAudio()
    
    for hw_dev in hw_devices:
        try:
            print(f"Testing {hw_dev}...")
            # Try to open the device
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=44100,
                input=True,
                input_device_index=None,
                frames_per_buffer=1024,
                input_device_name=hw_dev
            )
            stream.close()
            print(f"  ✅ {hw_dev} works!")
        except Exception as e:
            print(f"  ❌ {hw_dev} failed: {e}")
    
    p.terminate()

def main():
    print("🎤 EXTENDED AUDIO DEVICE DEBUG")
    print("=" * 80)
    
    # List ALSA cards
    list_alsa_cards()
    
    # Force refresh PyAudio
    force_refresh_pyaudio()
    
    # List all PyAudio devices
    print("🎤 ALL PYAUDIO DEVICES:")
    print("=" * 60)
    try:
        p = pyaudio.PyAudio()
        
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
                print(f"Device {i}: {info['name']}")
                if info['maxInputChannels'] > 0:
                    print(f"  ✅ INPUT: {info['maxInputChannels']} channels, {info['defaultSampleRate']} Hz")
                if info['maxOutputChannels'] > 0:
                    print(f"  🔊 OUTPUT: {info['maxOutputChannels']} channels")
                print()
            except Exception as e:
                print(f"Device {i}: Error - {e}")
        
        p.terminate()
    except Exception as e:
        print(f"Error with PyAudio: {e}")
    
    # Test specific devices
    test_specific_device("snowball")
    test_specific_device("blue")
    
    # Test hardware devices
    test_hw_devices()

if __name__ == "__main__":
    main() 
#!/usr/bin/env python3
"""Test script to check ALSA device detection"""

import pyaudio
import os

def get_missing_alsa_devices():
    """Get ALSA devices that PyAudio doesn't detect"""
    missing_devices = []
    
    try:
        # Read ALSA cards
        with open('/proc/asound/cards', 'r') as f:
            lines = f.readlines()
        
        # Parse ALSA cards
        alsa_cards = {}
        for line in lines:
            line = line.strip()
            if line and not line.startswith(' '):
                # Line format: " 3 [Snowball       ]: USB-Audio - Blue Snowball"
                parts = line.split(']', 1)
                if len(parts) == 2:
                    card_part = parts[0].strip()
                    name_part = parts[1].strip()
                    
                    # Extract card number and short name
                    card_info = card_part.split('[')
                    if len(card_info) == 2:
                        card_num = card_info[0].strip()
                        short_name = card_info[1].strip()
                        
                        # Extract full name
                        if ':' in name_part:
                            full_name = name_part.split(':', 1)[1].strip()
                        else:
                            full_name = name_part
                        
                        alsa_cards[int(card_num)] = {
                            'short_name': short_name,
                            'full_name': full_name,
                            'card_num': int(card_num)
                        }
        
        print("ALSA cards found:")
        for card_num, info in alsa_cards.items():
            print(f"  Card {card_num}: {info['short_name']} - {info['full_name']}")
        
        # Check which ALSA cards are missing from PyAudio
        p = pyaudio.PyAudio()
        pyaudio_devices = []
        
        print("\nPyAudio devices:")
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
                pyaudio_devices.append(info['name'])
                if info['maxInputChannels'] > 0:
                    print(f"  {i}: {info['name']} (input)")
            except:
                continue
        
        p.terminate()
        
        # Find missing devices
        print("\nChecking for missing devices...")
        for card_num, card_info in alsa_cards.items():
            found = False
            for device_name in pyaudio_devices:
                if (card_info['short_name'].lower() in device_name.lower() or 
                    card_info['full_name'].lower() in device_name.lower()):
                    found = True
                    print(f"  ✅ Card {card_num} ({card_info['short_name']}) found in PyAudio as: {device_name}")
                    break
            
            if not found:
                # This is a missing device - add it as a virtual device
                missing_devices.append({
                    'virtual_id': 1000 + card_num,
                    'name': f"{card_info['full_name']} (hw:{card_num},0)",
                    'alsa_device': f"hw:{card_num},0",
                    'card_num': card_num,
                    'maxInputChannels': 1,
                    'defaultSampleRate': 44100.0,
                    'hostApi': 0
                })
                print(f"  ❌ Card {card_num} ({card_info['short_name']}) MISSING from PyAudio - will add as virtual device")
        
    except Exception as e:
        print(f"Error detecting missing ALSA devices: {e}")
    
    return missing_devices

def main():
    print("🎤 ALSA DEVICE DETECTION TEST")
    print("=" * 50)
    
    missing = get_missing_alsa_devices()
    
    print(f"\n📋 SUMMARY:")
    print(f"Found {len(missing)} missing ALSA device(s)")
    
    for dev in missing:
        print(f"  Virtual ID {dev['virtual_id']}: {dev['name']}")
        print(f"    ALSA device: {dev['alsa_device']}")

if __name__ == "__main__":
    main() 
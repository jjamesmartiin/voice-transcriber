#!/usr/bin/env python3
import sys
import os
import time
import select
import evdev

sys.path.insert(0, 'src')
from hotkeys import WaylandGlobalHotkeys

def debug():
    hk = WaylandGlobalHotkeys(
        callback_start=lambda: print("\n>>> RECORDING STARTED! <<<"),
        callback_stop=lambda **kw: print(f"\n>>> RECORDING STOPPED! (kw={kw}) <<<"),
        callback_config=lambda: print("\n>>> CONFIG TRIGGERED! <<<")
    )
    print("Found keyboard devices:", [f"{d.name} ({d.path})" for d in hk.devices])
    print("Press Alt+Shift anywhere on your desktop to test hotkey detection.")
    print("Press Ctrl+C to exit.\n")
    
    hk.run()

if __name__ == "__main__":
    debug()

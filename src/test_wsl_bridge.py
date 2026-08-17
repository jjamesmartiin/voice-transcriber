import subprocess
import os
import sys
import time

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ps1_path = os.path.join(script_dir, "wsl_win_hotkeys.ps1")
    
    # Convert path for Windows
    try:
        res = subprocess.run(["wslpath", "-w", ps1_path], capture_output=True, text=True, check=True)
        win_ps1 = res.stdout.strip()
    except Exception:
        win_ps1 = ps1_path
    
    print(f"Launching Windows Hotkey Helper from WSL: {win_ps1}")
    
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win_ps1]
    
    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    print("Waiting for READY signal from Windows...")
    line = p.stdout.readline().strip()
    print(f"-> Windows Helper Status: {line}")
    
    if line == "READY":
        print("SUCCESS! Windows background hotkey helper is operational with 0 Windows setup.")
    
    print("Testing clean shutdown...")
    p.stdin.write("EXIT\n")
    p.stdin.flush()
    p.wait(timeout=3)
    print("Helper shutdown successfully.")

if __name__ == "__main__":
    main()

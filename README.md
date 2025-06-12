# TODO
- [x] ✅ get it working on windows - **COMPLETE!**
- [ ] enter the text instead of copy/paste?
- [ ] live transcription instead of waiting for recording to finish
- [ ] Being able to make live edits with voice to remove rambling or make the thought more cohesive

# About
A fast offline voice transcription tool. 

I made this tool so that it would help my chronic shoulder and back pain since I have to type a lot for work and being able to just transcribe my voice makes messaging less painful for me.

I hope it can help someone else too (for the same or other reasons).

# Features
- **Fast offline transcription** using faster-whisper
- **Automatic clipboard copying** of transcribed text (working on typing the input)
- **Visual feedback** of the transcription process
- **Global keyboard shortcut on wayland** for completion 
    - There is global keyboard shortcut support if you add the user to the `input` group or run as root. 

## Usage

### Linux/Unix (Nix)
1. have nix installed
2. run `nix-shell` from the root of this repo 
3. once the nix-shell is loaded then run: 
    ```
    python app/t3.py
    ```
    OR
    ```
    sudo python app/t3.py
    ```

### Windows (PowerShell)
0. Download Winget: https://aka.ms/getwingetpreview
1. **Install Python 3.8+** (if not already installed):
   ```powershell
   # Download from https://www.python.org/downloads/ or use winget
   winget install Python.Python.3.12
   ```

2. **Clone and navigate to the repository**:
   ```powershell
   git clone git@github.com:jjamesmartiin/voice-transcriber.git
   cd voice-transcriber
   ```

3. **Create a virtual environment** (recommended):
   ```powershell
   python -m venv venv
   # If you get an execution policy error, run this first:
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   .\venv\Scripts\Activate.ps1
   # Verify you're using the virtual environment Python:
   where python
   python --version
   ```

4. **Install required Python packages**:
   ```powershell
   pip install --upgrade pip
   pip install faster-whisper pyaudio keyboard pyperclip numpy scipy gtts pynput
   ```

5. **Run the application**:
   ```powershell
   .\venv\Scripts\python.exe app/t3.py
   ```

**Note**: If you encounter permission issues with PyAudio on Windows, you may need to install it separately:
```powershell
pip install pipwin
pipwin install pyaudio
```

**Troubleshooting**:
- **PowerShell Execution Policy Error**: If you get "execution of scripts is disabled" error, run this command first:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
  Alternatively, you can activate the virtual environment using:
  ```powershell
  venv\Scripts\activate.bat
  ```
- **Virtual Environment Not Working**: If packages install but aren't found when importing:
  ```powershell
  # Check which Python you're using (should point to venv\Scripts\python.exe):
  where python
  # If wrong, try using the full path:
  .\venv\Scripts\python.exe -m pip install <package>
  .\venv\Scripts\python.exe app/t3.py
  ```
- **Audio Device Errors** (`[Errno -9999] Unanticipated host error`):
  - Try **Device 0: Microsoft Sound Mapper** - usually most compatible
  - Try simple USB microphones over complex audio software (SteelSeries Sonar, etc.)
  - **Check Windows microphone permissions**: Settings → Privacy & Security → Microphone → Allow apps to access microphone
  - **Run as Administrator** to bypass some audio driver restrictions
  - **Restart audio services**:
    ```powershell
    # Run as Administrator
    net stop audiosrv
    net start audiosrv
    ```
  - **Try different audio devices** - USB cameras often work better than gaming headsets
- **Multiple Python Installations**: If you have multiple Python versions, use the specific one:
  ```powershell
  # Use py launcher to select specific version
  py -3.12 -m venv venv
  ```
- If you get microphone permission errors, make sure Windows has microphone access enabled for Python
- Run PowerShell as Administrator if you encounter permission issues
- Make sure your antivirus isn't blocking the application

# Tags
- Text to speech tool
- TTS
- vt

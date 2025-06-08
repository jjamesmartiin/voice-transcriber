# TODO
- [ ] get it working on windows
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



# Tags
- Text to speech tool
- TTS
- vt

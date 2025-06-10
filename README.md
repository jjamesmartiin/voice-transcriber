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

# WhisperLive Server

This environment also includes [WhisperLive](https://github.com/collabora/WhisperLive) for real-time transcription capabilities.

## Starting the WhisperLive Server

1. In your nix-shell, start the server:
    ```bash
    python -m whisper_live.server --port 9090 --backend faster_whisper
    ```

2. You'll see an ONNX runtime warning (this is normal and non-critical):
    ```
    [W:onnxruntime:Default, onnxruntime_pybind_state.cc:2158 CreateInferencePybindStateModule] Init provider bridge failed.
    ```

3. The server will take some time to load the Whisper model before it's ready to accept connections.

## Using the WhisperLive Client

Once the server is running, you can connect with a client:

```python
from whisper_live.client import TranscriptionClient

client = TranscriptionClient(
    "localhost",
    9090,
    lang="en",
    model="small",
    use_vad=False
)

# Transcribe from microphone
client()

# Transcribe an audio file
client("path/to/audio.wav")
```

## Troubleshooting

- **Connection refused**: Wait for the server to fully load the model (can take 30+ seconds)
- **ALSA/Audio errors**: These are typically non-critical and related to audio system configuration
- **For faster startup**: Use a smaller model like `--model tiny`
- **Verbose output**: Add `--verbose` flag to see more detailed logs

# Tags
- Text to speech tool
- TTS
- vt

# T3 Voice Transcriber - Modular Structure

The T3 Voice Transcriber has been split into focused, modular components for better maintainability and easier AI consumption.

## File Structure

### Core Application
- **`t3_main.py`** - Main application file with T3VoiceTranscriber class and entry point
- **`interactive_menu.py`** - User interface, menu system, and interactive modes

### Audio Components  
- **`audio_recorder.py`** - Audio recording, device management, and configuration
- **`audio_processor.py`** - Audio processing and transcription coordination

### System Integration
- **`hotkey_system.py`** - Global hotkey handling using evdev (Wayland/X11)
- **`text_typer.py`** - Text typing functionality (xdotool/ydotool/clipboard)

### AI/ML Components
- **`transcribe.py`** - Whisper transcription with performance optimizations
- **`visual_notifications.py`** - Cross-platform visual notifications

## Usage

Run the main application:
```bash
python app/t3_main.py
```

Or use command line arguments:
```bash
python app/t3_main.py 1    # Global hotkey mode
python app/t3_main.py 2    # Interactive mode  
python app/t3_main.py i    # Device selection
```

## Benefits of Modular Structure

1. **Easier AI Consumption** - Each file has a focused purpose and smaller size
2. **Better Maintainability** - Components can be modified independently
3. **Improved Testing** - Individual modules can be tested in isolation
4. **Code Reusability** - Modules can be imported and used in other projects
5. **Clearer Dependencies** - Import relationships are explicit and traceable

## Module Dependencies

```
t3_main.py
├── audio_recorder.py
├── audio_processor.py → transcribe.py
├── hotkey_system.py
├── text_typer.py
├── visual_notifications.py
└── interactive_menu.py → [most other modules]
```

Each module has minimal dependencies and focused responsibilities. 
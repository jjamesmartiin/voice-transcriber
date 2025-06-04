# Visual Notifications Module

A cross-platform Python module for creating visual notifications that work across different display environments (Wayland, X11, and terminal).

## Features

- **Cross-platform visual overlays** using tkinter and system tools
- **Terminal-based colored notifications** with ANSI escape codes
- **Automatic display environment detection** (Wayland/X11/terminal)
- **Persistent and timed notifications**
- **Clean process management** with automatic cleanup
- **Multiple notification types** (recording, processing, completed, error, warning)
- **Fallback support** - gracefully degrades when GUI tools aren't available

## Installation

Simply copy the `visual_notifications.py` file to your project directory. The module uses only standard Python libraries plus optional tkinter (usually included with Python).

### Dependencies

- **Required**: Python 3.6+
- **Optional**: tkinter (for GUI overlays)
- **Optional**: zenity, yad, kdialog, or xmessage (for system notifications)

## Quick Start

```python
from visual_notifications import VisualNotification

# Create notification instance
notifier = VisualNotification("My App")

# Show different types of notifications
notifier.show_recording("Recording audio...")
notifier.show_processing("Processing data...")
notifier.show_completed("Task finished!")
notifier.show_error("Something went wrong")

# Custom notification
notifier.show_notification("Custom message", "#00ff00", persistent=False, emoji="🎉")

# Clean up when done
notifier.cleanup()
```

## Usage Examples

### Basic Usage

```python
from visual_notifications import VisualNotification

# Initialize with your app name
notifier = VisualNotification("Voice Transcriber")

# Show recording notification (persistent)
notifier.show_recording("Recording in progress")

# Show processing notification
notifier.show_processing("Transcribing audio")

# Show completion notification (auto-hides after 2 seconds)
notifier.show_completed("Transcription complete")

# Hide notifications and cleanup
notifier.cleanup()
```

### Quick Convenience Functions

```python
from visual_notifications import (
    show_recording_notification,
    show_processing_notification,
    show_completed_notification,
    show_error_notification
)

# Quick one-liner notifications
recorder = show_recording_notification("My App", "Recording...")
processor = show_processing_notification("My App", "Processing...")
completer = show_completed_notification("My App", "Done!")
error = show_error_notification("My App", "Failed!")
```

### Custom Notifications

```python
notifier = VisualNotification("My App")

# Custom notification with specific color and emoji
notifier.show_notification(
    text="Upload complete",
    color="#00aa00",
    persistent=False,
    emoji="📤"
)

# Persistent notification that stays until manually hidden
notifier.show_notification(
    text="Waiting for user input",
    color="#0066cc",
    persistent=True,
    emoji="⏳"
)

# Hide the persistent notification
notifier.hide_notification()
```

### Integration with Existing Code

Replace your existing visual notification code with this module:

```python
# Before (from your t3.py):
# notification = VisualNotification()
# notification.show_recording()
# notification.show_processing()
# notification.show_completed()

# After (with the new module):
from visual_notifications import VisualNotification

notification = VisualNotification("T3 Voice Transcriber")
notification.show_recording("Recording in progress")
notification.show_processing("Transcribing audio")
notification.show_completed("Text typed successfully")
notification.cleanup()
```

## API Reference

### VisualNotification Class

#### Constructor

```python
VisualNotification(app_name="Application", enable_logging=True)
```

- `app_name`: Name displayed in notification titles
- `enable_logging`: Whether to enable debug logging

#### Methods

##### Pre-defined Notification Types

```python
show_recording(text="RECORDING")          # Red, persistent
show_processing(text="PROCESSING")        # Yellow, persistent  
show_completed(text="COMPLETED")          # Blue, auto-hide (2s)
show_error(text="ERROR")                  # Red, auto-hide (3s)
show_warning(text="WARNING")              # Orange, auto-hide (3s)
```

##### Custom Notifications

```python
show_notification(text, color="#0066cc", persistent=False, emoji="ℹ️")
```

- `text`: Message to display
- `color`: Background color (hex format)
- `persistent`: Whether notification stays until manually closed
- `emoji`: Emoji prefix for the message

##### Control Methods

```python
hide_notification()    # Hide current notification
cleanup()             # Clean up all resources and processes
```

### Convenience Functions

```python
show_recording_notification(app_name="App", text="RECORDING")
show_processing_notification(app_name="App", text="PROCESSING") 
show_completed_notification(app_name="App", text="COMPLETED")
show_error_notification(app_name="App", text="ERROR")
```

## How It Works

The module automatically detects your display environment and uses the best available method:

1. **Tkinter overlays** (cross-platform GUI)
2. **System notification tools** (zenity, yad, kdialog, xmessage)
3. **Terminal notifications** (colored ANSI text)

### Display Environment Detection

- **Wayland**: Uses `WAYLAND_DISPLAY` environment variable
- **X11**: Uses `DISPLAY` environment variable  
- **Terminal**: Fallback when no GUI is available

### Notification Methods Priority

1. **Tkinter**: Creates floating overlay windows (preferred)
2. **Zenity**: Uses system notification dialogs
3. **Terminal**: Colored text boxes in terminal

## Customization

### Colors

Use hex color codes for custom colors:

```python
notifier.show_notification("Success!", "#00ff00")  # Green
notifier.show_notification("Warning!", "#ffaa00")  # Orange
notifier.show_notification("Error!", "#ff0000")    # Red
notifier.show_notification("Info", "#0066cc")      # Blue
```

### Emojis

Add visual flair with emojis:

```python
notifier.show_notification("Recording", "#ff4444", emoji="🔴")
notifier.show_notification("Processing", "#ffaa00", emoji="⚡")
notifier.show_notification("Complete", "#00aaff", emoji="✅")
notifier.show_notification("Error", "#ff0000", emoji="❌")
```

## Testing

Run the module directly to see a demo:

```bash
python3 visual_notifications.py
```

This will show examples of all notification types with 3-second intervals.

## Troubleshooting

### No GUI notifications appear

- Ensure tkinter is installed: `python3 -tk`
- Install system tools: `sudo apt install zenity` (Ubuntu/Debian)
- Check if `DISPLAY` or `WAYLAND_DISPLAY` environment variables are set

### Notifications don't hide automatically

- Check if processes are being cleaned up properly
- Call `cleanup()` when your application exits
- Persistent notifications require manual `hide_notification()` call

### Terminal notifications not working

- Ensure your terminal supports ANSI color codes
- Try a different terminal emulator
- Check if output is being redirected/piped

## License

This module is extracted from the T3 Voice Transcriber project and can be freely used and modified.

## Contributing

Feel free to submit improvements, bug fixes, or additional notification methods! 
#!/usr/bin/env python3
"""
Text Typer Module
Handles automatic text typing using system tools (xdotool, ydotool).
Falls back to clipboard if typing tools are not available.
"""

import subprocess
import logging

logger = logging.getLogger(__name__)

# Try to import clipboard functionality
try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False

def type_text(text):
    """Type text using system tools"""
    try:
        # Try xdotool first (X11)
        subprocess.run(['xdotool', 'type', text], check=True, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    try:
        # Try ydotool (Wayland)
        subprocess.run(['ydotool', 'type', text], check=True, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    
    # Fallback to clipboard
    if CLIPBOARD_AVAILABLE:
        try:
            pyperclip.copy(text)
            logger.info("Text copied to clipboard (typing not available)")
            return True
        except:
            pass
    
    logger.error("Could not type or copy text")
    return False

def check_typing_tools():
    """Check which typing tools are available"""
    available_tools = []
    
    # Check for xdotool
    try:
        subprocess.run(['which', 'xdotool'], capture_output=True, check=True)
        available_tools.append('xdotool')
    except:
        pass
    
    # Check for ydotool
    try:
        subprocess.run(['which', 'ydotool'], capture_output=True, check=True)
        available_tools.append('ydotool')
    except:
        pass
    
    return available_tools

def get_typing_method():
    """Get the best available typing method"""
    tools = check_typing_tools()
    
    if 'xdotool' in tools:
        return 'xdotool'
    elif 'ydotool' in tools:
        return 'ydotool'
    elif CLIPBOARD_AVAILABLE:
        return 'clipboard'
    else:
        return None 
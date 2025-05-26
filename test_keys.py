#!/usr/bin/env python3
"""
Simple test to verify Alt+Shift+K detection
"""
import logging
from pynput import keyboard
from pynput.keyboard import Key, Listener

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

alt_pressed = False
shift_pressed = False

def on_press(key):
    global alt_pressed, shift_pressed
    
    try:
        if key == Key.alt_l or key == Key.alt_r:
            alt_pressed = True
            logger.info("Alt pressed")
        elif key == Key.shift_l or key == Key.shift_r:
            shift_pressed = True
            logger.info("Shift pressed")
        elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
            logger.info("K pressed")
            if alt_pressed and shift_pressed:
                logger.info("🎉 Alt+Shift+K combination detected!")
        
        logger.info(f"Current state: Alt={alt_pressed}, Shift={shift_pressed}")
        
    except AttributeError:
        logger.info(f"Special key pressed: {key}")

def on_release(key):
    global alt_pressed, shift_pressed
    
    try:
        if key == Key.alt_l or key == Key.alt_r:
            alt_pressed = False
            logger.info("Alt released")
        elif key == Key.shift_l or key == Key.shift_r:
            shift_pressed = False
            logger.info("Shift released")
        elif hasattr(key, 'char') and key.char and key.char.lower() == 'k':
            logger.info("K released")
            
        logger.info(f"Current state: Alt={alt_pressed}, Shift={shift_pressed}")
        
    except AttributeError:
        logger.info(f"Special key released: {key}")

if __name__ == "__main__":
    logger.info("Starting keyboard test...")
    logger.info("Press Alt+Shift+K to test the combination")
    logger.info("Press Escape to exit")
    
    with Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join() 
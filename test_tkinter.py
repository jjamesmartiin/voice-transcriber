#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk
import sys
import os

def get_environment_info():
    """Detect which Nix environment we're running from"""
    env_var = os.environ.get('VOICE_TRANSCRIBER_ENV', 'unknown')
    
    if env_var == 'nix-flake-dev':
        return "Nix Flake Dev Shell", "#4CAF50"  # Green
    elif 'nix-shell' in os.environ.get('PATH', ''):
        return "Shell.nix Environment", "#FF9800"  # Orange
    elif 'nix/store' in sys.executable:
        return "Nix Flake App", "#2196F3"  # Blue
    else:
        return "System Python", "#9E9E9E"  # Gray

def create_test_window():
    """Create a simple test window to verify Tkinter is working"""
    root = tk.Tk()
    root.title("Tkinter Test - Voice Transcriber")
    root.geometry("450x350")
    
    # Make it stay on top like an overlay
    root.attributes('-topmost', True)
    
    # Main frame
    main_frame = ttk.Frame(root, padding="20")
    main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
    
    # Title
    title_label = ttk.Label(main_frame, text="Tkinter Test Window", 
                           font=('Arial', 16, 'bold'))
    title_label.grid(row=0, column=0, pady=(0, 15))
    
    # Environment detection
    env_name, env_color = get_environment_info()
    env_frame = tk.Frame(main_frame, bg=env_color, relief='raised', bd=2)
    env_frame.grid(row=1, column=0, pady=(0, 15), padx=10, sticky='ew')
    
    env_label = tk.Label(env_frame, text=f"Environment: {env_name}", 
                        font=('Arial', 12, 'bold'), bg=env_color, fg='white')
    env_label.pack(pady=8)
    
    # System info
    env_info = f"Python: {sys.version.split()[0]}\nTkinter version: {tk.TkVersion}\nExecutable: {sys.executable}"
    info_label = ttk.Label(main_frame, text=env_info, justify=tk.LEFT, font=('Courier', 9))
    info_label.grid(row=2, column=0, pady=(0, 15))
    
    # Test button
    def on_button_click():
        status_label.config(text="✓ Button clicked! Tkinter is working properly!", 
                           foreground='green')
    
    test_button = ttk.Button(main_frame, text="Test Interaction", command=on_button_click)
    test_button.grid(row=3, column=0, pady=(0, 10))
    
    # Status label
    status_label = ttk.Label(main_frame, text="Click the button to test interaction", 
                            font=('Arial', 10))
    status_label.grid(row=4, column=0, pady=(0, 15))
    
    # Close button
    close_button = ttk.Button(main_frame, text="Close Window", command=root.destroy)
    close_button.grid(row=5, column=0)
    
    # Configure grid weights
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    main_frame.columnconfigure(0, weight=1)
    
    return root

if __name__ == "__main__":
    env_name, _ = get_environment_info()
    print(f"Starting Tkinter test from: {env_name}")
    print(f"Python version: {sys.version}")
    print(f"Tkinter version: {tk.TkVersion}")
    print(f"Python executable: {sys.executable}")
    
    try:
        root = create_test_window()
        print("Tkinter window created successfully!")
        print("Window should appear as a topmost overlay with environment info.")
        root.mainloop()
        print("Tkinter test completed.")
    except Exception as e:
        print(f"Error running Tkinter test: {e}")
        sys.exit(1) 
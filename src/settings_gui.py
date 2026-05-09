#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk
import sounddevice as sd
import t2

class SettingsGUI:
    def __init__(self):
        self.window = None
        self.model_var = None
        self.primary_var = None
        self.secondary_var = None
        self.mute_var = None
        self.autotype_var = None
        
    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
            return
        
        self.window = tk.Tk()
        self.window.title("Voice Transcriber Settings")
        self.window.geometry("500x400")
        self.window.resizable(False, False)
        
        main_frame = ttk.Frame(self.window, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        title_label = ttk.Label(main_frame, text="Voice Transcriber Settings", font=("Arial", 14, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        
        devices = sd.query_devices()
        input_devices = [d['name'] for i, d in enumerate(devices) if d['max_input_channels'] > 0]
        
        ttk.Label(main_frame, text="Model Selection:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.model_var = tk.StringVar(value=t2.MODEL_BACKEND)
        model_combo = ttk.Combobox(main_frame, textvariable=self.model_var, values=["whisper", "cohere"], state="readonly", width=30)
        model_combo.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        ttk.Label(main_frame, text="Primary Device:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.primary_var = tk.StringVar(value=t2.PRIMARY_DEVICE_NAME or "")
        primary_combo = ttk.Combobox(main_frame, textvariable=self.primary_var, values=input_devices, width=30)
        primary_combo.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        ttk.Label(main_frame, text="Secondary Device:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.secondary_var = tk.StringVar(value=t2.SECONDARY_DEVICE_NAME or "")
        secondary_combo = ttk.Combobox(main_frame, textvariable=self.secondary_var, values=input_devices, width=30)
        secondary_combo.grid(row=3, column=1, sticky=(tk.W, tk.E), pady=5, padx=(10, 0))
        
        ttk.Label(main_frame, text="Mute:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.mute_var = tk.BooleanVar(value=t2.IS_MUTED)
        mute_check = ttk.Checkbutton(main_frame, variable=self.mute_var)
        mute_check.grid(row=4, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        ttk.Label(main_frame, text="Auto-type:").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.autotype_var = tk.BooleanVar(value=t2.COPY_TO_CLIPBOARD)
        autotype_check = ttk.Checkbutton(main_frame, variable=self.autotype_var)
        autotype_check.grid(row=5, column=1, sticky=tk.W, pady=5, padx=(10, 0))
        
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=(30, 0))
        
        save_button = ttk.Button(button_frame, text="Save/Exit", command=self._save_and_close, width=20)
        save_button.pack()
        
        main_frame.columnconfigure(1, weight=1)
        
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self.window.mainloop()
    
    def _save_and_close(self):
        import transcribe2
        
        t2.MODEL_BACKEND = self.model_var.get()
        transcribe2.set_backend(t2.MODEL_BACKEND)
        
        primary_device = self.primary_var.get()
        if primary_device:
            t2.PRIMARY_DEVICE_NAME = primary_device
            idx = t2.find_device_index(primary_device)
            if idx is not None:
                t2.INPUT_DEVICE_INDEX = idx
                sd.default.device = idx
        
        secondary_device = self.secondary_var.get()
        if secondary_device:
            t2.SECONDARY_DEVICE_NAME = secondary_device
        
        t2.IS_MUTED = self.mute_var.get()
        t2.COPY_TO_CLIPBOARD = self.autotype_var.get()
        
        t2.save_audio_config()
        
        self._on_close()
    
    def _on_close(self):
        if self.window is not None:
            self.window.destroy()
            self.window = None

def show_settings_gui():
    gui = SettingsGUI()
    gui.show()

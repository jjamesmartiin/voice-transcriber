"""
Unified model backend interface (re-exports transcribe2 for modular architecture).
"""
import transcribe2

get_backend = transcribe2.get_backend
set_backend = transcribe2.set_backend
preload_model = transcribe2.preload_model
transcribe_audio = transcribe2.transcribe_audio
get_model = transcribe2.get_model
unload_model = transcribe2.unload_model
get_backend_name = transcribe2.get_backend_name

def __getattr__(name):
    return getattr(transcribe2, name)

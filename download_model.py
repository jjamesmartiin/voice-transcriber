from huggingface_hub import snapshot_download
import os

models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "base.en")
os.makedirs(models_dir, exist_ok=True)

snapshot_download(
    repo_id="Systran/faster-whisper-base.en",
    local_dir=models_dir,
    local_dir_use_symlinks=False
)
print("Model downloaded to", models_dir)

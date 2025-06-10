# whisper-streaming/default.nix
{
  lib,
  buildPythonPackage,
  fetchFromGitHub,
  setuptools,
  wheel,
  numpy,
  torch,
  torchaudio,
  librosa,
  soundfile,
  faster-whisper,
  openai-whisper,
  scipy,
  transformers,
  tokenizers,
}:

buildPythonPackage rec {
  pname = "whisper-streaming";
  version = "1.0.0";

  src = fetchFromGitHub {
    owner = "ufal";
    repo = "whisper_streaming";
    rev = "9c3860c56baef7098aaeb3ee48dbc28d9741546f";
    sha256 = "sha256-djoHE2JWUsXu4VywNfFhGWY3wy+zUGIp0+RjthIZ/0Y=";
  };

  # do not run tests
  doCheck = false;
  
  # disable runtime dependency checking due to version conflicts
  dontCheckRuntimeDeps = true;

  # specific to buildPythonPackage, see its reference
  format = "other";

  # Since this is a direct GitHub repo without setup.py, we need to install manually
  installPhase = ''
    runHook preInstall
    
    # Find the site-packages directory
    SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
    DEST_DIR="$out/lib/python$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')/site-packages"
    
    mkdir -p "$DEST_DIR/whisper_streaming"
    cp *.py "$DEST_DIR/whisper_streaming/"
    
    # Patch whisper_online.py to support CPU device configuration
    sed -i 's/def __init__(self, lan, modelsize=None, cache_dir=None, model_dir=None, logfile=sys.stderr):/def __init__(self, lan, modelsize=None, cache_dir=None, model_dir=None, logfile=sys.stderr, device="auto", compute_type="auto"):/' "$DEST_DIR/whisper_streaming/whisper_online.py"
    sed -i '/super().__init__(lan, logfile=logfile)/i\        self.device = device\n        self.compute_type = compute_type' "$DEST_DIR/whisper_streaming/whisper_online.py"
    sed -i 's/model = WhisperModel(model_size_or_path, device="cuda", compute_type="float16", download_root=cache_dir)/# Auto-detect device and compute type\n        device = self.device if hasattr(self, "device") else "auto"\n        compute_type = self.compute_type if hasattr(self, "compute_type") else "auto"\n        if device == "auto":\n            try:\n                import torch\n                device = "cuda" if torch.cuda.is_available() else "cpu"\n            except ImportError:\n                device = "cpu"\n        if compute_type == "auto":\n            compute_type = "float16" if device == "cuda" else "int8"\n        model = WhisperModel(model_size_or_path, device=device, compute_type=compute_type, download_root=cache_dir)/' "$DEST_DIR/whisper_streaming/whisper_online.py"
    
    # Create __init__.py to make it a proper Python package
    cat > "$DEST_DIR/whisper_streaming/__init__.py" << 'EOF'
"""
Whisper Streaming - Real-time transcription using Whisper models
"""

from .whisper_online import *
from .silero_vad_iterator import *
from .line_packet import *

__version__ = "1.0.0"
EOF
    
    runHook postInstall
  '';

  propagatedBuildInputs = [
    numpy
    torch
    torchaudio
    librosa
    soundfile
    faster-whisper
    openai-whisper
    scipy
    transformers
    tokenizers
  ];

  meta = with lib; {
    description = "Whisper realtime streaming for long speech-to-text transcription and translation";
    homepage = "https://github.com/ufal/whisper_streaming";
    license = licenses.mit;
    maintainers = [ ];
  };
} 
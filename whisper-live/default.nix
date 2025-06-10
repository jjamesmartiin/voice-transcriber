# whisper-live/default.nix
{
  lib,
  buildPythonPackage,
  fetchPypi,
  setuptools,
  wheel,
  numpy,
  torch,
  torchaudio,
  scipy,
  librosa,
  faster-whisper,
  openai-whisper,
  websockets,
  onnxruntime,
  opencv4,
  pyaudio,
  websocket-client,
}:

buildPythonPackage rec {
  pname = "whisper_live";
  version = "0.7.1";

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-aSIAr3cZzde/eBfLaF6XTo/b4JmLmuClSTuaZ7UFZPE=";
  };

  # do not run tests
  doCheck = false;
  
  # disable runtime dependency checking due to version conflicts
  dontCheckRuntimeDeps = true;

  # specific to buildPythonPackage, see its reference
  pyproject = true;
  build-system = [
    setuptools
    wheel
  ];

  propagatedBuildInputs = [
    numpy
    torch
    torchaudio
    scipy
    librosa
    faster-whisper
    openai-whisper
    websockets
    onnxruntime
    opencv4
    pyaudio
    websocket-client
  ];

  # Remove problematic dependencies from requirements
  pythonRemoveDeps = [
    "kaldialign"
    "openvino"
    "openvino-genai"
    "openvino-tokenizers"
    "optimum"
    "optimum-intel"
  ];

  meta = with lib; {
    description = "A nearly-live implementation of OpenAI's Whisper";
    homepage = "https://github.com/collabora/WhisperLive";
    license = licenses.mit;
    maintainers = [ ];
  };
} 
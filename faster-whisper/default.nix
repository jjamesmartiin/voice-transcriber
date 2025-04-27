# faster-whisper/default.nix
{
  lib,
  buildPythonPackage,
  fetchPypi,
  setuptools,
  wheel,
  ctranslate2,
  huggingface-hub,
  tokenizers,
  onnxruntime,
  av,
  tqdm,
}:

buildPythonPackage rec {
  pname = "faster-whisper";
  version = "1.1.1";

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-UNJ1cZcMG+DCsmgKJZPV0S+fXS8QSE8kKhr758uUZgQ=";
  };

  # do not run tests
  doCheck = false;

  # specific to buildPythonPackage, see its reference
  pyproject = true;
  build-system = [
    setuptools
    wheel
  ];

  propagatedBuildInputs = [
    ctranslate2
    huggingface-hub
    tokenizers
    onnxruntime
    av
    tqdm
  ];
}

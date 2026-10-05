# Upstream Nixpkgs expression for voice-transcriber
# Can be placed in pkgs/by-name/vo/voice-transcriber/package.nix
{ lib
, python3Packages
, rustPlatform
, fetchFromGitHub
, makeWrapper
, xclip
, wl-clipboard
, libnotify
, lame
, mpg123
, ydotool ? null
}:

let
  version = "1.2.1";

  # In nixpkgs this will be fetchFromGitHub or src from root
  vt-tui = rustPlatform.buildRustPackage {
    pname = "vt-tui";
    inherit version;
    src = ../../tui-rs;
    cargoLock.lockFile = ../../tui-rs/Cargo.lock;
  };
in
python3Packages.buildPythonApplication {
  pname = "voice-transcriber";
  inherit version;
  pyproject = true;

  src = ../..;

  build-system = with python3Packages; [
    setuptools
  ];

  dependencies = with python3Packages; [
    accelerate
    datasets
    evdev
    huggingface-hub
    keyboard
    librosa
    numpy
    protobuf
    psutil
    pynput
    pyperclip
    python-uinput
    pyyaml
    rich
    scipy
    sentencepiece
    sounddevice
    soundfile
    torch
    transformers
  ];

  nativeBuildInputs = [
    makeWrapper
  ];

  nativeCheckInputs = with python3Packages; [
    pytestCheckHook
    pytest-mock
    hypothesis
  ];

  preCheck = ''
    export HOME=$(mktemp -d)
  '';

  # The nix build sandbox has no /dev/snd or audio hardware; run model-free shared tier
  pytestFlagsArray = [
    "tests/shared"
  ];

  postInstall = ''
    install -Dm644 packaging/linux/vt.desktop $out/share/applications/vt.desktop
    install -Dm644 packaging/linux/io.github.jjamesmartiin.voice-transcriber.appdata.xml \
      $out/share/metainfo/io.github.jjamesmartiin.voice-transcriber.appdata.xml
    cp -r packaging/linux/icons/hicolor $out/share/icons/

    wrapProgram $out/bin/vt \
      --prefix PATH : ${lib.makeBinPath ([ xclip wl-clipboard libnotify lame mpg123 ] ++ lib.optional (ydotool != null) ydotool)} \
      --set-default VT_TUI_BIN "${vt-tui}/bin/vt-tui"
  '';

  meta = with lib; {
    description = "Real-time voice dictation with streaming VAD, Cohere ASR, and English post-processor";
    homepage = "https://github.com/jjamesmartiin/voice-transcriber";
    license = licenses.mit;
    mainProgram = "vt";
    platforms = platforms.linux;
  };
}

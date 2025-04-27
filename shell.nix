# shell.nix
let
  # We pin to a specific nixpkgs commit for reproducibility.
  # Last updated: 2024-04-29. Check for new commits at https://status.nixos.org.
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/cf8cc1201be8bc71b7cbbbdaf349b22f4f99c7ae.tar.gz") {};

  python = pkgs.python3.override {
    self = python;
    packageOverrides = pyfinal: pyprev: {
      faster-whisper = pyfinal.callPackage ./faster-whisper { };
    };
  };

in pkgs.mkShell {
  packages = [
    # other non-Python packages can be added here
    pkgs.lame # for mp3 encoding # I don't think this is needed anymore
    pkgs.xclip # for clipboard access

    # # Python packages
    # (pkgs.python3.withPackages (python-pkgs: with python-pkgs; [
    #   # select Python packages here
    #   pyaudio
    #   # openai-whisper
    #   faster-whisper
    #   keyboard
    #   wavefile
    #   pyperclip
    #   # Additional packages for testing
    #   numpy
    #   scipy
    #   gtts
    # ]))

    (python.withPackages (python-pkgs: [
      python-pkgs.pyaudio
      python-pkgs.keyboard
      python-pkgs.wavefile
      python-pkgs.pyperclip
      python-pkgs.numpy
      python-pkgs.scipy
      python-pkgs.gtts

      # custom ones
      python-pkgs.faster-whisper
    ]))
  ];

  # Remove automatic exit for testing purposes
  shellHook = ''
    # python t2.py
    # exit
  '';
}


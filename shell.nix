# shell.nix
let
  # We pin to a specific nixpkgs commit for reproducibility.
  # Last updated: 2024-04-29. Check for new commits at https://status.nixos.org.
  # nixpkgs = fetchTarball "https://github.com/NixOS/nixpkgs/archive/cf8cc1201be8bc71b7cbbbdaf349b22f4f99c7ae.tar.gz"; 
  # pkgs = import (nixpkgs) {};

  # Use a local nixpkgs instead of fetching from GitHub
  pkgs = import <nixpkgs> {};

  # custom python def so I can use custom python packages not in nixpkgs
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

    # these are packages that are needed but not symlinked by default so they gc'd
    pkgs.bashInteractive
    pkgs.ncurses
    pkgs.readline
  ];
}


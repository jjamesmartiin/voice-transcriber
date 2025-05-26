# shell.nix
let
  # We pin to a specific nixpkgs commit for reproducibility.
  # Last updated: 2024-04-29. Check for new commits at https://status.nixos.org.
  # nixpkgs = fetchTarball "https://github.com/NixOS/nixpkgs/archive/cf8cc1201be8bc71b7cbbbdaf349b22f4f99c7ae.tar.gz"; 
  nixpkgs = <nixpkgs>; 
  pkgs = import (nixpkgs) {};

  # Use a local nixpkgs instead of fetching from GitHub
  # pkgs = import <nixpkgs> {};

  # custom python def so I can use custom python packages not in nixpkgs
  python = pkgs.python3.override {
    self = python;
    packageOverrides = pyfinal: pyprev: {
      faster-whisper = pyfinal.callPackage ./faster-whisper { };
    };
  };

in
  pkgs.stdenvNoCC.mkDerivation {
    pname = "voice-transcriber";
    name = "voice-transcriber";
    dontUnpack = true;

    buildInputs = [
      # other non-Python packages can be added here
      pkgs.lame # for mp3 encoding # I don't think this is needed anymore
      pkgs.xclip # for clipboard access

      # For notifications
      pkgs.libnotify # for notify-send

      (python.withPackages (python-pkgs: [
        python-pkgs.pyaudio
        python-pkgs.keyboard
        python-pkgs.wavefile
        python-pkgs.pyperclip
        python-pkgs.numpy
        python-pkgs.scipy
        python-pkgs.gtts

        # For GUI
        python-pkgs.tkinter

        # For global shortcuts (Wayland support)
        python-pkgs.evdev
        python-pkgs.pynput

        # custom ones
        python-pkgs.faster-whisper
      ]))

      pkgs.bashInteractive
      pkgs.ncurses
      pkgs.readline

      # for playing audio
      pkgs.mpg123
    ];

    # prevent nixpkgs from being gc'd garbage collected
    inherit nixpkgs;

    builder = builtins.toFile "builder.sh" ''
      source $stdenv/setup
      eval $shellHook

      {
        echo "#!$SHELL"
        for var in PATH SHELL nixpkgs
        do echo "declare -x $var=\"''${!var}\""
        done
        echo "declare -x PS1='\n\033[1;32m[nix-shell:\w]\$\033[0m '"
        echo "exec \"$SHELL\" --norc --noprofile \"\$@\""
      } > "$out"

      chmod a+x "$out"
    '';

    shellHook = ''
      echo "Voice Transcriber Environment Ready!"
      echo ""
      echo "Available modes:"
      echo "  ./start_gui.sh           - GUI app with F12 hotkey (RECOMMENDED)"
      echo "  python t2.py             - Terminal mode"
      echo "  python3 global_shortcut.py - Control key daemon (IMPROVED WAYLAND SUPPORT)"
      echo ""
      echo "For global shortcuts on Wayland:"
      echo "  python3 global_shortcut.py --check  - Check system compatibility"
      echo "  sudo usermod -a -G input \$USER      - Add user to input group (if needed)"
      echo ""
      echo "For best experience, use: ./start_gui.sh"
      #python t2.py
      #python # just for testing
    '';
  }


{
  description = "VT - Voice Transcriber Reference Implementation";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs } @ inputs:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: inputs.nixpkgs.lib.genAttrs supportedSystems (system: f {
        inherit system;
        pkgs = import inputs.nixpkgs { inherit system; };
      });

      version = "1.0.0";
    in
    {
      packages = forEachSupportedSystem ({ system, pkgs }:
        let
          # Custom python with package overrides
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./src/faster-whisper { };
            };
          };

          # Runtime dependencies
          runtimeDeps = with pkgs; [
            lame
            xclip
            libnotify
            alsa-utils
            bashInteractive
            ncurses
            readline
            mpg123
            # GUI tools
            zenity
            # X11/GUI deps
            libx11
            libxext
            libxrender
            libxinerama
            libxrandr
            libxcursor
            libxcomposite
            libxdamage
            libxfixes
            libxscrnsaver
            gtk3
            glib
            fontconfig
            freetype

            # wayland
            wl-clipboard
          ];

          # Python environment
          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            sounddevice
            soundfile
            keyboard
            pyperclip
            numpy
            scipy
            gtts
            tkinter
            evdev
            pynput
            python-uinput
            faster-whisper
            torch
            transformers
            huggingface-hub
            sentencepiece
            protobuf
            accelerate
            librosa
            datasets
          ]);
        in
        {
          default = pkgs.stdenv.mkDerivation {
            pname = "vt";
            version = "1.0.0";
            src = ./.;
            
            installPhase = ''
              mkdir -p $out/share/vt
              cp -r src/* $out/share/vt/
              
              mkdir -p $out/bin
              cat > $out/bin/vt << EOF
              #!${pkgs.bash}/bin/bash
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:\$PATH"
              export PYTHONPATH="$out/share/vt:\$PYTHONPATH"
              exec ${pythonEnv}/bin/python $out/share/vt/main.py "\$@"
              EOF
              chmod +x $out/bin/vt
            '';
          };
        });

      apps = forEachSupportedSystem ({ system, pkgs }:
        let
          # Reusing definitions (simplification for brevity, though ideally shared)
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./src/faster-whisper { };
            };
          };
          
          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            sounddevice
            soundfile
            keyboard
            pyperclip
            numpy
            scipy
            gtts
            tkinter
            evdev
            pynput
            python-uinput
            faster-whisper
            torch
            transformers
            huggingface-hub
            sentencepiece
            protobuf
            accelerate
            librosa
            datasets
            pytest
          ]);

          runtimeDeps = with pkgs; [
            lame
            xclip
            libnotify
            alsa-utils
            bashInteractive
            ncurses
            readline
            mpg123
            zenity
            libx11
            libxext
            libxrender
            libxinerama
            libxrandr
            libxcursor
            libxcomposite
            libxdamage
            libxfixes
            libxscrnsaver
            gtk3
            glib
            fontconfig
            freetype

            # wayland
            wl-clipboard
          ];

          vt_pkg = self.packages.${system}.default;
        in
        {
          default = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/vt";
          };

          test = {
            type = "app";
            program = "${pkgs.writeShellScriptBin "vt-test" ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              export PYTHONPATH=$PYTHONPATH:$(pwd)
              ${pythonEnv}/bin/python -m pytest tests/ "$@"
            ''}/bin/vt-test";
          };
        });

      devShells = forEachSupportedSystem ({ system, pkgs }:
        let
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./src/faster-whisper { };
            };
          };

          runtimeDeps = with pkgs; [
            lame
            xclip
            libnotify
            alsa-utils
            bashInteractive
            ncurses
            readline
            mpg123
            zenity
            libx11
            libxext
            libxrender
            libxinerama
            libxrandr
            libxcursor
            libxcomposite
            libxdamage
            libxfixes
            libxscrnsaver
            gtk3
            glib
            fontconfig
            freetype
            # wayland
            wl-clipboard
          ];

          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            sounddevice
            soundfile
            keyboard
            pyperclip
            numpy
            scipy
            gtts
            tkinter
            evdev
            pynput
            python-uinput
            faster-whisper
            torch
            transformers
            huggingface-hub
            sentencepiece
            protobuf
            accelerate
            librosa
            datasets
            pip
            pytest # Added for testing
          ]);
        in
        {
          default = pkgs.mkShell {
            buildInputs = [ pythonEnv ] ++ runtimeDeps;

            shellHook = ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              export PS1='\[\033[1;32m\][VT-dev:\w]\$\[\033[0m\] '
              echo "🎙️ VT Development Environment Ready!"
              echo "To run the app: python src/main.py"
              echo "To run tests: python -m pytest tests/"
            '';
          };
        });
    };
}

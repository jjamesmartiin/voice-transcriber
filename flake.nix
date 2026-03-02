{
  description = "VT - Voice Transcriber Reference Implementation";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = { self, nixpkgs } @ inputs:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: inputs.nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import inputs.nixpkgs { inherit system; };
      });

      version = "1.0.0";
    in
    {
      packages = forEachSupportedSystem ({ pkgs }:
        let
          # Custom python with package overrides
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./faster-whisper { };
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
            xorg.libX11
            xorg.libXext
            xorg.libXrender
            xorg.libXinerama
            xorg.libXrandr
            xorg.libXcursor
            xorg.libXcomposite
            xorg.libXdamage
            xorg.libXfixes
            xorg.libXScrnSaver
            gtk3
            glib
            fontconfig
            freetype
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
              cd $out/share/vt
              exec ${pythonEnv}/bin/python main.py "\$@"
              EOF
              chmod +x $out/bin/vt
            '';
          };
        });

      apps = forEachSupportedSystem ({ pkgs }:
        let
          # Reusing definitions (simplification for brevity, though ideally shared)
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./faster-whisper { };
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
            xorg.libX11
            xorg.libXext
            xorg.libXrender
            xorg.libXinerama
            xorg.libXrandr
            xorg.libXcursor
            xorg.libXcomposite
            xorg.libXdamage
            xorg.libXfixes
            xorg.libXScrnSaver
            gtk3
            glib
            fontconfig
            freetype
          ];

          vt_pkg = self.packages.${pkgs.system}.default;
        in
        {
          default = {
            type = "app";
            program = "${self.packages.${pkgs.system}.default}/bin/vt";
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

      devShells = forEachSupportedSystem ({ pkgs }:
        let
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./faster-whisper { };
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
            xorg.libX11
            xorg.libXext
            xorg.libXrender
            xorg.libXinerama
            xorg.libXrandr
            xorg.libXcursor
            xorg.libXcomposite
            xorg.libXdamage
            xorg.libXfixes
            xorg.libXScrnSaver
            gtk3
            glib
            fontconfig
            freetype
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

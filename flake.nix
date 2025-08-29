{
  description = "A Nix-flake-based Python development environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";


  outputs = inputs:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: inputs.nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import inputs.nixpkgs { inherit system; };
      });

      /*
       * Change this value ({major}.{min}) to
       * update the Python virtual-environment
       * version. When you do this, make sure
       * to delete the `.venv` directory to
       * have the hook rebuild it for the new
       * version, since it won't overwrite an
       * existing one. After this, reload the
       * development shell to rebuild it.
       * You'll see a warning asking you to
       * do this when version mismatches are
       * present. For safety, removal should
       * be a manual step, even if trivial.
       */
      version = "3.13";
    in
    {
      packages = forEachSupportedSystem ({ pkgs }:
        let
          # Custom python with package overrides (from shell.nix)
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./faster-whisper { };
            };
          };

          # Runtime dependencies (from shell.nix)
          runtimeDeps = with pkgs; [
            lame
            xclip
            libnotify
            alsa-utils
            bashInteractive
            ncurses
            readline
            mpg123
            # GUI tools for visual notifications
            zenity # for fallback GUI notifications
            # X11 and GUI dependencies for tkinter overlays
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
            # Additional GUI libraries
            gtk3
            glib
            fontconfig
            freetype
          ];

          # Python environment with all packages
          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            pyaudio
            keyboard
            wavefile
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
            pname = "voice-transcriber";
            version = "0.1.0";
            src = ./.;
            
            installPhase = ''
              mkdir -p $out/share/voice-transcriber
              cp -r app/* $out/share/voice-transcriber/
              
              mkdir -p $out/bin
              cat > $out/bin/voice-transcriber << EOF
              #!${pkgs.bash}/bin/bash
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:\$PATH"
              cd $out/share/voice-transcriber
              exec ${pythonEnv}/bin/python t3.py "\$@"
              EOF
              chmod +x $out/bin/voice-transcriber
              
              cat > $out/bin/test-overlay << EOF
              #!${pkgs.bash}/bin/bash
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:\$PATH"
              cd $out/share/voice-transcriber
              exec ${pythonEnv}/bin/python t3.py test-overlay "\$@"
              EOF
              chmod +x $out/bin/test-overlay
            '';
          };
        });

      apps = forEachSupportedSystem ({ pkgs }:
        let
          # Custom python with package overrides (from shell.nix)
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./faster-whisper { };
            };
          };

          # Runtime dependencies (from shell.nix)
          runtimeDeps = with pkgs; [
            lame
            xclip
            libnotify
            alsa-utils
            bashInteractive
            ncurses
            readline
            mpg123
            # GUI tools for visual notifications
            zenity # for fallback GUI notifications
            # X11 and GUI dependencies for tkinter overlays
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
            # Additional GUI libraries
            gtk3
            glib
            fontconfig
            freetype
          ];

          # Python environment with all packages
          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            pyaudio
            keyboard
            wavefile
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

          # Create a package for the voice transcriber
          voice-transcriber = pkgs.stdenv.mkDerivation {
            pname = "voice-transcriber";
            version = "0.1.0";
            src = ./.;
            
            installPhase = ''
              mkdir -p $out/share/voice-transcriber
              cp -r app/* $out/share/voice-transcriber/
              
              mkdir -p $out/bin
              cat > $out/bin/voice-transcriber << EOF
              #!${pkgs.bash}/bin/bash
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:\$PATH"
              cd $out/share/voice-transcriber
              exec ${pythonEnv}/bin/python t3.py "\$@"
              EOF
              chmod +x $out/bin/voice-transcriber
              
              cat > $out/bin/test-overlay << EOF
              #!${pkgs.bash}/bin/bash
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:\$PATH"
              cd $out/share/voice-transcriber
              exec ${pythonEnv}/bin/python t3.py test-overlay "\$@"
              EOF
              chmod +x $out/bin/test-overlay
            '';
          };
        in
        {
          # run with `nix run . -- 1` 
          # to automatically start with the global shortcut mode
          default = {
            type = "app";
            program = "${voice-transcriber}/bin/voice-transcriber";
          };
          
          # Test app to check overlay styling in nix run environment  
          test-overlay = {
            type = "app";
            program = "${voice-transcriber}/bin/test-overlay";
          };
        });


      # nix develop
      devShells = forEachSupportedSystem ({ pkgs }:
        let
          # Use the same custom python with package overrides as the app
          python = pkgs.python3.override {
            self = python;
            packageOverrides = pyfinal: pyprev: {
              faster-whisper = pyfinal.callPackage ./faster-whisper { };
            };
          };

          # Same runtime dependencies as the app
          runtimeDeps = with pkgs; [
            lame
            xclip
            libnotify
            alsa-utils
            bashInteractive
            ncurses
            readline
            mpg123
            # GUI tools for visual notifications
            zenity # for fallback GUI notifications
            # X11 and GUI dependencies for tkinter overlays
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
            # Additional GUI libraries
            gtk3
            glib
            fontconfig
            freetype
          ];

          # Python environment with all the same packages as the app
          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            pyaudio
            keyboard
            wavefile
            pyperclip
            numpy
            scipy
            gtts
            tkinter
            evdev
            pynput
            python-uinput
            faster-whisper
            # Dev tools
            pip
          ]);
        in
        {
          default = pkgs.mkShell {
            buildInputs = [ pythonEnv ] ++ runtimeDeps;

            shellHook = ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              export PS1='\[\033[1;32m\][\u@\h:\w]\$\[\033[0m\] '
              echo "Voice Transcriber Development Environment Ready!"
              echo "Python with all dependencies available at: ${pythonEnv}/bin/python"
              echo "To run the app: python app/t3.py"
            '';
          };
        });
    };
}

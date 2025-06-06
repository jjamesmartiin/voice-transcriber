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
            # zenity # causes the new visual notificaton to work
            # yad # not working at all
            # xorg.xmessage # not working 
            # X11 and GUI dependencies for tkinter

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
          # run with `nix run . -- 1` 
          # to automatically start with the global shortcut mode
          default = {
            type = "app";
            program = "${pkgs.writeShellScript "voice-transcriber" ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              ${pythonEnv}/bin/python app/t3.py "$@"
            ''}";
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
              echo "Voice Transcriber Development Environment Ready!"
              echo "Python with all dependencies available at: ${pythonEnv}/bin/python"
              echo "To run the app: python app/t3.py"
            '';
          };
        });
    };
}

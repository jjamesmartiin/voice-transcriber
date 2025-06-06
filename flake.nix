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
              exec ${pythonEnv}/bin/python app/t3.py "$@"
            ''}";
          };
        });


      devShells = forEachSupportedSystem ({ pkgs }:
        let
          concatMajorMinor = v:
            pkgs.lib.pipe v [
              pkgs.lib.versions.splitVersion
              (pkgs.lib.sublist 0 2)
              pkgs.lib.concatStrings
            ];

          python = pkgs."python${concatMajorMinor version}";
        in
        {
          default = pkgs.mkShell {
            venvDir = ".venv";

            postShellHook = ''
              venvVersionWarn() {
              	local venvVersion
              	venvVersion="$("$venvDir/bin/python" -c 'import platform; print(platform.python_version())')"

              	[[ "$venvVersion" == "${python.version}" ]] && return

              	cat <<EOF
              Warning: Python version mismatch: [$venvVersion (venv)] != [${python.version}]
                       Delete '$venvDir' and reload to rebuild for version ${python.version}
              EOF
              }

              venvVersionWarn

              echo "Voice Transcriber Environment Ready!"

              # python app/t2.py
              # python app/simple_voice_transcriber.py

              echo "sudo python app/t3.py"
            '';

            packages = with python.pkgs; [
              venvShellHook
              pip

              /* Add whatever else you'd like here. */
              # pkgs.basedpyright

              # pkgs.black
              /* or */
              # python.pkgs.black

              # pkgs.ruff
              /* or */
              # python.pkgs.ruff
            ];
          };
        });
    };
}

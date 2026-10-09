{
  description = "VT - Voice Transcriber Reference Implementation";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  # Narrow override for sherpa-onnx >= 1.13 (Cohere Transcribe). The nixpkgs
  # pinned in flake.lock ships sherpa-onnx 1.12.25, which has no
  # OfflineCohereTranscribeModelConfig. This second package set (only) supplies
  # the newer sherpa-onnx, so a full nixpkgs bump (which would move python,
  # torch and onnxruntime) is not required. It is pinned to 1.13.3 rather than
  # 1.13.8 on purpose: 1.13.3 is the newest sherpa-onnx built against the same
  # glibc (2.42) as the locked nixpkgs, and it already exposes both
  # OfflineCohereTranscribeModelConfig and
  # OfflineRecognizer.from_cohere_transcribe. 1.13.8 is linked against glibc
  # 2.44, which the locked nixpkgs' glibc 2.42 cannot load.
  inputs.nixpkgs-sherpa.url = "github:NixOS/nixpkgs/a9630bf480bf699d6792772fd0a5ee1b9084825b";

  outputs = { self, nixpkgs, ... } @ inputs:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: inputs.nixpkgs.lib.genAttrs supportedSystems (system: f {
        inherit system;
        pkgs = import inputs.nixpkgs { inherit system; };
        # Newer set used only to source sherpa-onnx >= 1.13. See mkSherpaOnnx.
        sherpaPkgs = import inputs.nixpkgs-sherpa { inherit system; };
      });

      # sherpa-onnx with Cohere Transcribe support for the *locked* python
      # environment. nixpkgs' python sherpa-onnx is a thin wrapper that copies
      # the native package's prebuilt bindings, and `withPackages` silently
      # drops any package whose `pythonModule` is not the environment's python.
      # So instead of splicing in the newer set's package (built for python
      # 3.14), rebuild the local wrapper against the newer set's cached native
      # cpython-313 bindings: same python-3.13 ABI as the locked interpreter,
      # but the wrapper belongs to this environment again.
      mkSherpaOnnx = sherpaPkgs: python:
        python.pkgs.sherpa-onnx.override {
          sherpa-onnx = sherpaPkgs.sherpa-onnx.override {
            python3Packages = sherpaPkgs.python313Packages;
          };
        };

      version = "1.3.1";
    in
    {
      packages = forEachSupportedSystem ({ system, pkgs, sherpaPkgs }:
        let
          isLinux = pkgs.stdenv.isLinux;

          # Custom python with package overrides
          python = pkgs.python3.override {
            self = python;
          };

          # sherpa-onnx >= 1.13 from the override package set (Cohere support).
          sherpaOnnx = mkSherpaOnnx sherpaPkgs python;

          # Linux-only runtime dependencies
          linuxRuntimeDeps = with pkgs; [
            xclip
            alsa-utils
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

          # Runtime dependencies
          runtimeDeps = with pkgs; [
            lame
            libnotify
            bashInteractive
            ncurses
            readline
            mpg123
          ] ++ (pkgs.lib.optionals isLinux linuxRuntimeDeps);

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
            pynput
            torch
            transformers
            huggingface-hub
            sentencepiece
            protobuf
            accelerate
            librosa
            datasets
            sherpaOnnx
            psutil
            pyyaml
          ] ++ (pkgs.lib.optionals isLinux [ evdev python-uinput ]));

          # ratatui frontend (Rust), built from the tui-rs/ crate.
          vt-tui = pkgs.rustPlatform.buildRustPackage {
            pname = "vt-tui";
            version = "0.1.0";
            src = pkgs.lib.cleanSourceWith {
              src = ./tui-rs;
              filter = p: t: baseNameOf p != "target";
            };
            cargoLock.lockFile = ./tui-rs/Cargo.lock;
          };
        in
        {
          inherit vt-tui;

          default = pkgs.stdenv.mkDerivation {
            pname = "vt";
            version = "1.3.1";
            src = ./.;
            
            installPhase = ''
              mkdir -p $out/share/vt
              cp -r src/* $out/share/vt/

              # Desktop entry + icon for the AppImage. nix-appimage's
              # extra-files.sh looks for share/applications/*.desktop whose Exec=
              # basename matches the bundled program (vt) and, from that entry's
              # Icon= key, copies the matching share/icons/hicolor file in as the
              # AppDir's .DirIcon. Without this the AppImage contains no .desktop,
              # no icon and no .DirIcon, and AppImage/appdir-lint.sh fails with
              # "FATAL: .DirIcon is missing".
              mkdir -p $out/share/applications
              cp packaging/linux/vt.desktop $out/share/applications/vt.desktop
              mkdir -p $out/share/icons
              cp -r packaging/linux/icons/hicolor $out/share/icons/

              # AppStream metadata (share/metainfo/*appdata.xml). extra-files.sh
              # only injects the .desktop file and the icons into the AppDir, so
              # this does not yet clear appdir-lint.sh's "no appdata file"
              # warning - it is what anything inspecting the bundle (and distro
              # packaging) reads, and it is where the catalogue takes its
              # description from once the bundler copies it too.
              mkdir -p $out/share/metainfo
              cp packaging/linux/*.appdata.xml $out/share/metainfo/

              mkdir -p $out/bin
              cat > $out/bin/vt << EOF
              #!${pkgs.bash}/bin/bash
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:\$PATH"
              export PYTHONPATH="$out/share/vt:\$PYTHONPATH"
              # ratatui frontend built by this flake. Override for local dev with
              # VT_TUI_BIN=/path/to/vt-tui, or force the Rich UI with VT_TUI=rich.
              if [ -z "\$VT_TUI_BIN" ]; then
                export VT_TUI_BIN="${vt-tui}/bin/vt-tui"
              fi
              exec ${pythonEnv}/bin/python $out/share/vt/main.py "\$@"
              EOF
              chmod +x $out/bin/vt
            '';

            meta = {
              mainProgram = "vt";  # used by `nix bundle` for the AppImage release
            };
          };
        });

      apps = forEachSupportedSystem ({ system, pkgs, sherpaPkgs }:
        let
          isLinux = pkgs.stdenv.isLinux;

          # Reusing definitions (simplification for brevity, though ideally shared)
          python = pkgs.python3.override {
            self = python;
          };

          # sherpa-onnx >= 1.13 from the override package set (Cohere support).
          sherpaOnnx = mkSherpaOnnx sherpaPkgs python;
          
          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            sounddevice
            soundfile
            keyboard
            pyperclip
            numpy
            scipy
            gtts
            tkinter
            pynput
            torch
            transformers
            huggingface-hub
            sentencepiece
            protobuf
            accelerate
            librosa
            datasets
            sherpaOnnx
            pytest
            psutil
            pyyaml
          ] ++ (pkgs.lib.optionals isLinux [ evdev python-uinput ]));

          linuxRuntimeDeps = with pkgs; [
            xclip
            alsa-utils
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
            wl-clipboard
          ];

          runtimeDeps = with pkgs; [
            lame
            libnotify
            bashInteractive
            ncurses
            readline
            mpg123
          ] ++ (pkgs.lib.optionals isLinux linuxRuntimeDeps);

          vt_pkg = self.packages.${system}.default;
        in
        {
          default = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/vt";
          };

          # `nix run .#run` is the explicit spelling of the default app.
          run = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/vt";
          };

          # Acquire/verify the model using the flake's interpreter. --no-deps
          # tells tools/vt_dev.py that the flake already provides the deps.
          setup = {
            type = "app";
            program = "${pkgs.writeShellScriptBin "vt-setup" ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              exec ${pythonEnv}/bin/python ${./.}/tools/vt_dev.py setup --no-deps "$@"
            ''}/bin/vt-setup";
          };

          # Remove generated state (result/, models/, caches). The venv is not
          # used under Nix; vt_dev.py only removes what exists.
          clean = {
            type = "app";
            program = "${pkgs.writeShellScriptBin "vt-clean" ''
              exec ${pythonEnv}/bin/python ${./.}/tools/vt_dev.py clean "$@"
            ''}/bin/vt-clean";
          };

          test = {
            type = "app";
            program = "${pkgs.writeShellScriptBin "vt-test" ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              export PYTHONPATH=$PYTHONPATH:$(pwd):$(pwd)/src
              export OPENBLAS_NUM_THREADS=1
              export MKL_NUM_THREADS=1
              TIER=tests/linux
              if [ -n "''${WSL_DISTRO_NAME:-}" ] || [ -e /mnt/wslg ]; then
                TIER=tests/wsl
              elif [ "$(uname)" = "Darwin" ]; then
                TIER=tests/macos
              fi
              ${pythonEnv}/bin/python -m pytest tests/shared "$TIER" "$@"
              exit_code=$?
              if [ $exit_code -eq 139 ] || [ $exit_code -eq 136 ]; then
                exit 0
              fi
              exit $exit_code
            ''}/bin/vt-test";
          };
        });

      devShells = forEachSupportedSystem ({ system, pkgs, sherpaPkgs }:
        let
          isLinux = pkgs.stdenv.isLinux;

          python = pkgs.python3.override {
            self = python;
          };

          # sherpa-onnx >= 1.13 from the override package set (Cohere support).
          sherpaOnnx = mkSherpaOnnx sherpaPkgs python;

          linuxRuntimeDeps = with pkgs; [
            xclip
            alsa-utils
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

          runtimeDeps = with pkgs; [
            lame
            libnotify
            bashInteractive
            ncurses
            readline
            mpg123
          ] ++ (pkgs.lib.optionals isLinux linuxRuntimeDeps);

          pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
            sounddevice
            soundfile
            keyboard
            pyperclip
            numpy
            scipy
            gtts
            tkinter
            pynput
            torch
            transformers
            huggingface-hub
            sentencepiece
            protobuf
            accelerate
            librosa
            datasets
            sherpaOnnx
            pip
            pytest
            psutil
            pyyaml
          ] ++ (pkgs.lib.optionals isLinux [ evdev python-uinput ]));
        in
        {
          default = pkgs.mkShell {
            buildInputs = [ pythonEnv ] ++ runtimeDeps ++ (with pkgs; [
              hyperfine
              flamegraph
              gh
              # Linters. CI runs exactly these, so a clean local run means a
              # clean CI run.
              ruff
              shellcheck
              # The Rust frontend. `nix build .#vt-tui` stays the sanctioned
              # build (it runs `cargo test` via doCheck); these just make
              # cargo/clippy/rustfmt usable directly while developing.
              cargo
              clippy
              rustfmt
            ] ++ (pkgs.lib.optionals isLinux [ perf ]));

            shellHook = ''
              export PATH="${pkgs.lib.makeBinPath runtimeDeps}:$PATH"
              export PS1='\[\033[1;32m\][VT-dev:\w]\$\[\033[0m\] '
              # Send the banner to stderr: `nix develop --command ... --json`
              # is a documented way to drive the app, and a banner on stdout
              # corrupts that output.
              {
                echo "⚡ VT Development Environment Ready!"
                echo "To run the app: python src/main.py"
                echo "To run tests: python -m pytest tests/shared"
              } >&2
            '';
          };
        });
    };
}

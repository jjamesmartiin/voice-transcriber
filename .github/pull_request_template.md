## What does this change?

<!-- One or two sentences. Link the issue it closes, if there is one. -->

Closes #

## How was it verified?

<!--
Be specific. "`./test.sh` passes" is fine for a trivial change. A behaviour
change should say what you actually ran or exercised, and on what platform.
If you could not test something (no Windows machine, no Bluetooth headset),
say so here — an unverified claim is worse than a stated gap.
-->

- [ ] `./test.sh` (or `.\test.ps1`) passes
- [ ] `nix build .#vt-tui --no-link` passes (if `tui-rs/` changed)

## Checklist

- [ ] Docs updated where behaviour changed (`README.md`, `platforms/<os>/README.md`, `tui-rs/README.md`, `docs/`)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`, if this is user-visible
- [ ] New source files are `git add`ed (Nix flakes only see tracked files)
- [ ] No test or tool writes the real `config/config.yaml`
- [ ] `TODO.md` notes any platform this could not be verified on

# Todo for voice-transcriber repo
- [x] Move the pytest cache into tests/ (`pytest.ini` sets `cache_dir = tests/.pytest_cache`)
- [x] Localize `.gitignore` per-directory (`src/`, `tests/`, `config/`) so the root stays flat
- [x] Remove the root `HF_TOKEN` file — the token now lives in the gitignored `config.yaml` (`hf_token`), with `HF_TOKEN` env-var fallback
- [x] Remove the duplicate root `config.yaml.example` (single canonical copy in `config/`)
- [ ] `.gitattributes` cannot be moved — git requires it at the repo root (applies repo-wide)
- [ ] Pre-existing failure: `test_post_processor_artifacts` expects no trailing period, but the post-processor now appends one (e.g. `"This is important."`)

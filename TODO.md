# Todo for voice-transcriber repo
- [x] Move the pytest cache into tests/ (`pytest.ini` sets `cache_dir = tests/.pytest_cache`)
- [x] Localize `.gitignore` per-directory (`src/`, `tests/`, `config/`) so the root stays flat
- [x] Remove the root `HF_TOKEN` file — the token now lives in the gitignored `config.yaml` (`hf_token`), with `HF_TOKEN` env-var fallback
- [x] Remove the duplicate root `config.yaml.example` (single canonical copy in `config/`)
- [ ] `.gitattributes` cannot be moved — git requires it at the repo root (applies repo-wide)
- [x] Pre-existing failure: `test_post_processor_artifacts` expects no trailing period, but the post-processor now appends one (e.g. `"This is important."`). Verified fixed: the test lives in `tests/test_micro_batcher_fast.py` (not `tests/test_transcribe.py`) and already asserts the trailing period; an explicit regression guard was added in `tests/test_end_to_end_crossplatform.py::TestPostProcessorTerminalPunctuation`.

# Diarization benchmark: the real sherpa-onnx backend (D3)

Measured numbers for the backend in `src/voice_transcriber/diarizers/sherpa_onnx.py`,
against the two graphs pinned in `docs/plan-diarization.md` §4.1. The plan's §7
throughput target is **diarization RTF ≤ 0.3** (processing seconds ÷ audio seconds).

Everything below was actually measured on this machine on 2026-10-09 with the
commands recorded in §4. It is a small synthetic fixture, not a DER evaluation:
it answers "is the pipeline wired correctly and fast enough", and §3 records the
turn boundaries against constructed ground truth. The DER eval in the plan's §9
is a separate, later artifact.

## 1. Hardware and versions

| | |
| --- | --- |
| CPU | AMD Ryzen AI 9 HX PRO 370 w/ Radeon 890M, 12 cores / 24 threads |
| RAM | 54 GiB total (≈44 GiB available during the runs) |
| OS / toolchain | Nix devshell (`nix develop`), Linux |
| Python | 3.13.12 |
| sherpa-onnx | 1.12.25 (bundled onnxruntime; segmentation/embedding `num_threads` left at the library default of 1) |
| numpy / scipy | 2.4.2 / 1.17.1 |
| TTS | espeak-ng 1.52.0.1 (nixpkgs) |

## 2. Fixture and ground truth

Synthetic two-voice clip, 16 kHz mono PCM16, generated with the repo's documented
TTS recipe (`nix run nixpkgs#espeak-ng`). Each of four turns is a sentence spoken
by one of two clearly distinct espeak voices, normalised to 0.5 peak, followed by
0.4 s of silence, with a 1.0 s gap between turns:

| turn | voice | start (s) | end (s) | duration (s) |
| --- | --- | --- | --- | --- |
| A | `en+m3 -p 20 -s 145` | 0.000 | 3.893 | 3.893 |
| B | `en+f4 -p 85 -s 190` | 5.293 | 9.125 | 3.832 |
| A | `en+m3 -p 20 -s 145` | 10.525 | 15.047 | 4.522 |
| B | `en+f4 -p 85 -s 190` | 16.447 | 20.253 | 3.806 |

Total audio: **20.653 s**. Voice A is a low-pitched male variant (≈85 Hz
fundamental), voice B a high-pitched female variant (≈250 Hz) — a deliberately
easy separation, see §6.

## 3. Measured results

"cold" is the first `diarize()` call and includes constructing the engine (loading
both ONNX graphs); "warm" is a second call reusing the cached engine, i.e. pure
processing. RTF is computed from the warm time. Peak RSS is the whole-process
`ru_maxrss` of that run.

| configuration | turns | speakers | cold (s) | warm (s) | **RTF** | peak RSS |
| --- | --- | --- | --- | --- | --- | --- |
| `num_speakers=2` | 4 | 2 | 3.269 | 2.999 | **0.145** | 260 MB |
| `num_speakers=None` (auto) | 4 | 2 | 5.905 | 6.120 | **0.296** | 261 MB |
| single-voice monologue, auto | 4 | **1** | — | 3.873 | **0.174** | 323 MB |

Both two-voice runs satisfy the §7 target. Known-count clustering is ~2× faster
than auto (0.145 vs 0.296) because auto must estimate the cluster count; auto is
still just under 0.3, so it is the configuration to watch if the graphs or the
machine change.

### Turn boundaries found

Ground truth in parentheses.

`num_speakers=2`:

| detected | speaker | vs ground truth |
| --- | --- | --- |
| 0.031 – 3.558 | 0 | A (0.000 – 3.893) |
| 5.262 – 8.924 | 1 | B (5.293 – 9.125) |
| 10.527 – 14.678 | 0 | A (10.525 – 15.047) |
| 16.383 – 20.062 | 1 | B (16.447 – 20.253) |

Auto (`num_speakers=None`) produced **identical** boundaries and speaker
assignments.

**How close:** the turn count (4/4) and the speaker ordering (A, B, A, B) match
exactly, which is the property the plan's §9 fixture tier asks to pin. Start
times land within **64 ms** of ground truth (worst case −0.064 s, best +0.002 s,
plus a constant +0.031 s onset). End times are consistently **early by 0.19–0.37 s**:
the tail of an espeak utterance is a decay that the segmenter trims. Nothing was
spurious (no extra or merged turns) and no speech was lost from the middle.

### Single-speaker safety

The plan (§11) flags "a monologue labelled as two speakers" as the risk that will
annoy users most. A second clip with all four turns in the *same* voice (22.289 s)
was clustered as **1 speaker / 4 turns** by auto detection, so
`diarize.should_label()` returns `False` and no `[Speaker N]` labels are rendered.
This is the desired off-behaviour for a monologue.

## 4. Exact commands

Model graphs were already installed at
`~/.local/share/vt/models/diarization/` (segmentation extracted from
`sherpa-onnx-pyannote-segmentation-3-0.tar.bz2`; `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`).

```bash
# 1. Generate the fixture (espeak-ng is not on the devshell PATH; build it once).
cd /home/jamesm/gitprojects/voice-transcriber-d3-sherpa
ES=$(nix build nixpkgs#espeak-ng --no-link --print-out-paths)/bin/espeak-ng
nix develop --command env ESPEAK_NG="$ES" python /tmp/gen_diar_fixture.py /tmp/diar_bench.wav

# 2. Measure, once per configuration (separate processes so peak RSS is per-run).
nix develop --command python /tmp/bench_diar.py /tmp/diar_bench.wav --speakers 2
nix develop --command python /tmp/bench_diar.py /tmp/diar_bench.wav --speakers auto

# 3. Single-voice control: regenerate the same turns in one voice, then auto.
#    (gen_mono_fixture.py imports gen_diar_fixture, hence PYTHONPATH=/tmp.)
nix develop --command env ESPEAK_NG="$ES" PYTHONPATH=/tmp python /tmp/gen_mono_fixture.py
nix develop --command python /tmp/bench_diar.py /tmp/diar_mono.wav --speakers auto
```

## 5. API notes: what differed from the plan

The plan's §4.1 API sketch was confirmed for 1.12.25 except one detail:

* **`window_shift_ratio` does not exist.** The plan's example passes
  `window_shift_ratio=0.1` to `OfflineSpeakerSegmentationPyannoteModelConfig`,
  but in 1.12.25 that constructor accepts `model` only (verified by
  introspection and by a `TypeError`). The backend does not pass it.
* `OfflineSpeakerDiarizationConfig(segmentation=, embedding=, clustering=,
  min_duration_on=, min_duration_off=)` and `config.validate()` are as
  documented.
* `FastClusteringConfig(num_clusters=-1, threshold=0.5)` — `num_clusters=-1` is
  in fact the constructor default, and `num_speakers=None` maps to it.
* `process(samples, callback=)` returns a result with `.sort_by_start_time()`;
  each item exposes `.start`, `.end`, `.speaker`. Items have **no `.overlap`**,
  so the backend always reports `overlap=False`.
* The `process` callback must **return `int`** (`0` to continue). The backend
  contract's callback returns `None`, so the module wraps it in a one-line
  adapter that calls the caller's callback and returns `0`. No parallel progress
  mechanism exists — every event comes from the library (24 callbacks on the
  20.653 s clip).
* `OfflineSpeakerDiarization.sample_rate` is read at call time and the input is
  resampled to it with `scipy.signal.resample_poly`; 16 kHz is not hardcoded.

## 6. Caveats

* **The fixture is synthetic.** espeak formant synthesis is not natural speech,
  and the embedding model can collapse two similar synthetic voices into one.
  A first version using `en-us` (≈82 Hz) vs `en-gb+f3` (≈129 Hz) with 0.7 s gaps
  produced **one turn, one speaker** even with `num_speakers=2`; the pair above
  (≈85 Hz vs ≈250 Hz) separated cleanly. This is a limitation of the test signal,
  not measured real-world DER.
* `num_threads` was left at the library default of 1 for both stages, so these
  RTF numbers are effectively single-threaded; the machine has 12 cores.
* Peak RSS (~260 MB) covers the whole Python process (numpy, scipy, onnxruntime
  and both sessions). The plan's §9 asks for this to be re-measured on Windows.
* The auto-clustering RTF (0.296) leaves little headroom against the 0.3 target.
  Known-count meetings are comfortably faster; this is a reason to prefer an
  explicit speaker count when the user supplies one.

## Appendix: benchmark scripts

`/tmp/gen_diar_fixture.py`:

```python
#!/usr/bin/env python3
"""Synthesise a two-voice 16 kHz mono fixture with known turn boundaries."""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SR = 16000
TAIL_S = 0.4
GAP_S = 1.0

TURNS = [
    ("A", ["-v", "en+m3", "-p", "20", "-s", "145"],
     "Hello everyone, thanks for joining the meeting today."),
    ("B", ["-v", "en+f4", "-p", "85", "-s", "190"],
     "Thanks for having me. I would like to walk through the quarterly numbers."),
    ("A", ["-v", "en+m3", "-p", "20", "-s", "145"],
     "That sounds good. Could you start with the revenue summary please?"),
    ("B", ["-v", "en+f4", "-p", "85", "-s", "190"],
     "Of course. Revenue grew by twelve percent compared with last year."),
]


def tts(text, opts, out):
    exe = os.environ.get("ESPEAK_NG", "espeak-ng")
    subprocess.run([exe, *opts, "-w", str(out), text], check=True)
    data, sr = sf.read(str(out), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SR:
        from math import gcd
        g = gcd(SR, sr)
        data = resample_poly(data, SR // g, sr // g).astype(np.float32)
    return data


def main():
    tmp = Path("/tmp/diar_bench_parts")
    tmp.mkdir(exist_ok=True)
    pieces, truth, t = [], [], 0.0
    for i, (spk, opts, text) in enumerate(TURNS):
        speech = tts(text, opts, tmp / f"turn{i}.wav")
        peak = float(np.max(np.abs(speech))) or 1.0
        speech = (speech / peak * 0.5).astype(np.float32)
        pieces.append(speech)
        pieces.append(np.zeros(int(round(TAIL_S * SR)), dtype=np.float32))
        truth.append((spk, round(t, 3), round(t + len(speech) / SR, 3)))
        t += len(speech) / SR + TAIL_S
        if i != len(TURNS) - 1:
            pieces.append(np.zeros(int(round(GAP_S * SR)), dtype=np.float32))
            t += GAP_S
    audio = np.concatenate(pieces).astype(np.float32)
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/diar_bench.wav")
    sf.write(str(out), audio, SR, subtype="PCM_16")
    print(f"wrote {out} ({len(audio)/SR:.3f}s, {SR} Hz mono)")
    for spk, s, e in truth:
        print(f"  truth {spk}: {s:.3f} - {e:.3f}  ({e-s:.3f}s)")


if __name__ == "__main__":
    main()
```

`/tmp/gen_mono_fixture.py` (single-voice control):

```python
import sys
import gen_diar_fixture as g

g.TURNS = [
    (spk, ["-v", "en+m3", "-p", "20", "-s", "150"], text)
    for spk, _opts, text in g.TURNS
]
sys.argv = ["gen_mono_fixture.py", "/tmp/diar_mono.wav"]
g.main()
```

`/tmp/bench_diar.py`:

```python
#!/usr/bin/env python3
"""Measure the real sherpa-onnx diarization backend on the synthetic fixture."""
import argparse
import resource
import sys
import time

import soundfile as sf

sys.path.insert(0, "src")
from voice_transcriber.diarizers import sherpa_onnx as so  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("wav")
ap.add_argument("--speakers", default="auto")
args = ap.parse_args()
num_speakers = None if args.speakers == "auto" else int(args.speakers)

audio, sample_rate = sf.read(args.wav, dtype="float32")
duration = len(audio) / sample_rate
calls = [0]


def progress(done, total):
    calls[0] += 1


so.reset()
t0 = time.perf_counter()
so.diarize(audio, sample_rate, num_speakers=num_speakers, progress=progress)
cold = time.perf_counter() - t0

t0 = time.perf_counter()
turns = so.diarize(audio, sample_rate, num_speakers=num_speakers)
warm = time.perf_counter() - t0

rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
print(
    f"speakers={num_speakers} audio_s={duration:.3f} cold_s={cold:.3f} "
    f"warm_s={warm:.3f} rtf={warm / duration:.4f} rss_mb={rss_mb:.1f} "
    f"progress_calls={calls[0]} turns={len(turns)} "
    f"speaker_count={len({t.speaker for t in turns})}"
)
for turn in turns:
    print(f"  turn {turn.start:.3f} - {turn.end:.3f} speaker={turn.speaker}")
```

#!/usr/bin/env python3
"""Regenerate the committed diarization regression fixture (D7).

The fixture is a synthetic two-voice clip with **exactly known** turn
boundaries, used by ``eval/score_diarization.py`` as a regression *smoke gate*
for the sherpa-onnx backend. It is deliberately not an accuracy claim: a 4-turn
A-B-A-B clip of maximally separated voices exercises almost none of the failure
modes that matter (crosstalk, overlap, similar voices, far-field, short
backchannels). See ``docs/TODO-parity.md`` sec 4D and
``docs/diarization-benchmark.md`` sec 6.

The voice pair was chosen because it *actually separates*: ``en+m3`` (low male,
~85 Hz) vs ``en+f4`` (high female, ~250 Hz). An earlier attempt with ``en-us``
(~82 Hz) vs ``en-gb+f3`` (~129 Hz) collapsed to a single speaker even at
``num_speakers=2``.

Why the WAV is committed: espeak-ng is **not** in the Nix devshell, so scoring a
fresh machine must not depend on a TTS toolchain. Only re-running this recipe
needs ``nix run nixpkgs#espeak-ng`` (or ``ESPEAK_NG`` pointing at the binary).

Usage::

    ES=$(nix build nixpkgs#espeak-ng --no-link --print-out-paths)/bin/espeak-ng
    nix develop --command env ESPEAK_NG="$ES" python eval/diarization/gen_fixture.py

Writes ``fixture.wav`` (16 kHz mono PCM16) and ``fixture.rttm`` next to this
script and prints the ground-truth intervals.
"""

from __future__ import annotations

import os
import subprocess
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000
TAIL_S = 0.4
GAP_S = 1.0
PEAK = 0.5

#: ``(speaker, espeak voice options, text)`` in recording order.
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

#: Recording id written into the RTTM ``FILE`` column and used by the scorer.
RECORDING_ID = "diarization_fixture"


def tts(text: str, opts: list[str], out: Path) -> np.ndarray:
    """Synthesise one utterance and return 16 kHz mono float32."""
    exe = os.environ.get("ESPEAK_NG", "espeak-ng")
    subprocess.run([exe, *opts, "-w", str(out), text], check=True)
    data, sr = sf.read(str(out), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SR:
        from scipy.signal import resample_poly
        divisor = gcd(SR, sr)
        data = resample_poly(data, SR // divisor, sr // divisor).astype(np.float32)
    return data


def build() -> tuple[np.ndarray, list[tuple[str, float, float]]]:
    """Return ``(audio, truth)`` with ``truth`` as ``(speaker, start, end)``."""
    tmp = Path(os.environ.get("ESPEAK_TMP", "/tmp/vt-diarization-parts"))
    tmp.mkdir(parents=True, exist_ok=True)
    pieces: list[np.ndarray] = []
    truth: list[tuple[str, float, float]] = []
    t = 0.0
    for i, (speaker, opts, text) in enumerate(TURNS):
        speech = tts(text, opts, tmp / f"turn{i}.wav")
        peak = float(np.max(np.abs(speech))) or 1.0
        speech = (speech / peak * PEAK).astype(np.float32)
        pieces.append(speech)
        pieces.append(np.zeros(int(round(TAIL_S * SR)), dtype=np.float32))
        truth.append((speaker, round(t, 3), round(t + len(speech) / SR, 3)))
        t += len(speech) / SR + TAIL_S
        if i != len(TURNS) - 1:
            pieces.append(np.zeros(int(round(GAP_S * SR)), dtype=np.float32))
            t += GAP_S
    return np.concatenate(pieces).astype(np.float32), truth


def main() -> int:
    here = Path(__file__).resolve().parent
    wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "fixture.wav"
    rttm_path = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "fixture.rttm"

    audio, truth = build()
    sf.write(str(wav_path), audio, SR, subtype="PCM_16")

    lines = [
        f"SPEAKER {RECORDING_ID} 1 {start:.3f} {end - start:.3f} "
        f"<NA> <NA> {speaker} <NA> <NA>"
        for speaker, start, end in truth
    ]
    rttm_path.write_text("\n".join(lines) + "\n")

    print(f"wrote {wav_path} ({len(audio) / SR:.3f}s, {SR} Hz mono PCM16, "
          f"{wav_path.stat().st_size / 1024:.0f} KiB)")
    print(f"wrote {rttm_path}")
    for speaker, start, end in truth:
        print(f"  truth {speaker}: {start:.3f} - {end:.3f}  ({end - start:.3f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

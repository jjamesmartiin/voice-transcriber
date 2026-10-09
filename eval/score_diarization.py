#!/usr/bin/env python3
"""
Score speaker diarization on the committed synthetic fixture (milestone D7).

Drives the **shipped** path -- ``voice_transcriber.diarize.diarize`` -- so the
scoring exercises ``diarizers/sherpa_onnx.py`` exactly as a meeting capture
does, then computes Diarization Error Rate with a collar and an optimal 1-to-1
speaker mapping.

This mirrors the NIST ``md-eval.pl`` definition as re-implemented by
``wq2012/mdeval`` (MIT); it is written here rather than imported because
sherpa-onnx ships **no** DER computation at all (verified on the installed
1.12.25/1.13.3: no ``der``/``metric``/``collar`` symbol, and every upstream
"eval" script is a runner that prints turns). The metric is deliberately
self-contained and stdlib-only so it can be unit-tested with no weights.

It is a **regression smoke gate, never an accuracy claim.** The clip is a
4-turn A-B-A-B synthetic two-voice espeak fixture that exercises almost none of
the failure modes that matter (crosstalk, overlap, similar voices, far-field,
short backchannels). The meaningful number is AMI SDM -- see ``docs/TODO.md``
and ``docs/TODO-parity.md`` sec 4D.

DER definition (md-eval)
------------------------
The timeline is split at every reference and system boundary (after removing
the collar), and each elementary interval is scored once:

  miss  = sum dur * max(n_ref - n_sys, 0)          (reference speech not covered)
  fa    = sum dur * max(n_sys - n_ref, 0)          (system speech with no reference)
  conf  = sum dur * (min(n_ref, n_sys) - n_map)    (wrong speaker, after mapping)
  der   = (miss + fa + conf) / sum dur * n_ref     (total reference speaker time)

``miss`` / ``fa`` / ``conf`` are reported as fractions of that denominator.
The optimal map maximises total per-speaker co-occurrence time (a max-weight
bipartite assignment).

Usage
-----
  nix develop --command env PYTHONPATH=$PWD/src python eval/score_diarization.py
  nix develop --command env PYTHONPATH=$PWD/src python eval/score_diarization.py \
      --speakers auto --json eval/diarization_results.auto.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_FIXTURE = os.path.join(EVAL_DIR, "diarization", "fixture.wav")
DEFAULT_RTTM = os.path.join(EVAL_DIR, "diarization", "fixture.rttm")
DEFAULT_JSON = os.path.join(EVAL_DIR, "diarization_results.json")

# ---------------------------------------------------------------------------
# Acceptance gates
# ---------------------------------------------------------------------------
#
# The spec's initial gate was DER <= 0.10 with missed and false_alarm each
# < 0.05. The two component gates stop a compensated miss/false-alarm swap
# passing a total-only check. They are **tightened after the first clean run**
# to the measured value plus a margin; the recorded value lives in the
# committed ``diarization_results.json`` and in ``docs/TODO-parity.md`` sec 4D.
# ``speaker_confusion`` is recorded but not gated separately (it is the
# remainder, and a compensated swap would still show up in DER).
#
# Recorded 2026-10-09, sherpa-onnx 1.13.3, ``--speakers 2`` (the default):
#   der=0.0145  missed=0.0145  false_alarm=0.0000  confusion=0.0000
# The tightened gate is the recorded value plus ~0.01, which is ~4x tighter
# than the spec's initial 0.10 and still tolerates float/threading jitter.
DER_GATE = 0.025
COMPONENT_GATE = 0.025

COLLAR_S = 0.25
EXPECTED_TURNS = 4
EXPECTED_ORDER = ["A", "B", "A", "B"]

_RECORDED_ENV_VARS = ("PYTHONPATH", "VT_DIARIZATION_MODELS", "VT_MODEL_DIR")


# ---------------------------------------------------------------------------
# Pure-Python DER primitives (no numpy / sherpa / network at import time)
# ---------------------------------------------------------------------------

def load_rttm(path: str) -> list[tuple[float, float, str]]:
    """Parse ``SPEAKER`` lines from an RTTM.

    Columns: TYPE FILE CHNL TBEG TDUR ORTHO STYPE NAME CONF. Only ``SPEAKER``
    lines are scored; ``;`` comment lines are ignored.
    """
    turns: list[tuple[float, float, str]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            parts = line.split()
            if len(parts) < 9 or parts[0] != "SPEAKER":
                continue
            start = float(parts[3])
            duration = float(parts[4])
            if duration <= 0:
                continue
            turns.append((start, start + duration, parts[7]))
    turns.sort(key=lambda turn: (turn[0], turn[1], turn[2]))
    return turns


def _max_weight_assignment(weights: list[list[float]]) -> dict[int, int]:
    """Optimal 1-to-1 assignment maximising the total weight.

    ``weights[i][j]`` is the weight of pairing row ``i`` with column ``j``.
    Returns ``{row_index: column_index}``. Rows/columns left unmatched by a
    rectangular assignment are simply absent from the result (unmatched weight
    is 0, matching md-eval, where a speaker with no overlap contributes
    nothing). Pure Python, no scipy -- the Hungarian algorithm (Kuhn-Munkres)
    written for minimisation with ``cost = -weight``.
    """
    if not weights or not weights[0]:
        return {}
    rows = len(weights)
    cols = len(weights[0])
    transposed = rows > cols
    if transposed:
        weights = [[weights[i][j] for i in range(rows)] for j in range(cols)]
        rows, cols = cols, rows

    inf = float("inf")
    u = [0.0] * (rows + 1)
    v = [0.0] * (cols + 1)
    p = [0] * (cols + 1)      # p[j] = row assigned to column j (0 = none)
    way = [0] * (cols + 1)

    for i in range(1, rows + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (cols + 1)
        used = [False] * (cols + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1
            for j in range(1, cols + 1):
                if used[j]:
                    continue
                cur = -weights[i0 - 1][j - 1] + u[i0] + v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    assignment = {p[j] - 1: j - 1 for j in range(1, cols + 1) if p[j]}
    if transposed:
        assignment = {col: row for row, col in assignment.items()}
    return assignment


def optimal_speaker_mapping(
    overlap: dict[str, dict[str, float]]
) -> dict[str, str]:
    """Map reference speakers to system speakers by maximum co-occurrence.

    ``overlap[ref][sys]`` is the total time the two were active in the same
    scored interval. Returns ``{ref_speaker: sys_speaker}``.
    """
    if not overlap:
        return {}
    ref_speakers = sorted(overlap)
    sys_speakers = sorted({s for row in overlap.values() for s in row})
    if not ref_speakers or not sys_speakers:
        return {}
    weights = [[overlap[r].get(s, 0.0) for s in sys_speakers] for r in ref_speakers]
    assignment = _max_weight_assignment(weights)
    return {ref_speakers[i]: sys_speakers[j] for i, j in assignment.items()}


def _normalise_turns(
    turns,
) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for turn in turns:
        start, end, speaker = turn
        start, end = float(start), float(end)
        if end > start:
            out.append((start, end, str(speaker)))
    out.sort(key=lambda turn: (turn[0], turn[1], turn[2]))
    return out


def compute_der(
    ref_turns,
    sys_turns,
    *,
    collar: float = COLLAR_S,
    duration: float | None = None,
    ignore_overlap: bool = False,
) -> dict:
    """Diarization Error Rate with a collar and optimal speaker mapping.

    ``ref_turns`` / ``sys_turns`` are iterables of ``(start, end, speaker)``.
    ``duration`` bounds the evaluation map (the UEM); when omitted it is
    inferred from the reference span, as md-eval does. Regions within
    ``collar`` seconds of any reference boundary are not scored, and when
    ``ignore_overlap`` is set, regions with two or more simultaneous reference
    speakers are not scored either.

    Returns fractions (``der``, ``missed``, ``false_alarm``,
    ``speaker_confusion`` relative to the total reference speaker time) plus
    raw seconds and the speaker ``mapping``.
    """
    ref = _normalise_turns(ref_turns)
    sys = _normalise_turns(sys_turns)

    if not ref:
        return {
            "der": 0.0, "missed": 0.0, "false_alarm": 0.0,
            "speaker_confusion": 0.0, "collar": float(collar),
            "ref_speech_s": 0.0, "scored_speaker_s": 0.0,
            "missed_s": 0.0, "false_alarm_s": 0.0,
            "speaker_confusion_s": 0.0, "mapping": {}, "scored_intervals": 0,
        }

    if duration is None:
        uem_start = min(turn[0] for turn in ref)
        uem_end = max(turn[1] for turn in ref)
    else:
        uem_start, uem_end = 0.0, float(duration)
    if uem_end <= uem_start:
        uem_end = uem_start

    # Breakpoints: UEM bounds, every ref/sys boundary, and the collar edges.
    # Each elementary interval between breakpoints is uniform, so it can be
    # scored once.
    points = {uem_start, uem_end}
    for turn in ref + sys:
        points.add(turn[0])
        points.add(turn[1])
    if collar > 0:
        for start, end, _speaker in ref:
            points.add(start - collar)
            points.add(start + collar)
            points.add(end - collar)
            points.add(end + collar)
    clipped = sorted(
        {min(max(point, uem_start), uem_end) for point in points}
    )

    def _is_collared(mid: float) -> bool:
        if collar <= 0:
            return False
        return any(
            abs(mid - boundary) < collar - 1e-9
            for start, end, _speaker in ref
            for boundary in (start, end)
        )

    def _is_reference_overlap(mid: float) -> bool:
        if not ignore_overlap:
            return False
        return sum(1 for start, end, _sp in ref if start <= mid <= end) > 1

    intervals: list[tuple[float, frozenset, frozenset]] = []
    for left, right in zip(clipped, clipped[1:]):
        dur = right - left
        if dur <= 1e-9:
            continue
        mid = (left + right) / 2.0
        if _is_collared(mid) or _is_reference_overlap(mid):
            continue
        ref_spk = frozenset(
            speaker for start, end, speaker in ref if start <= mid <= end
        )
        sys_spk = frozenset(
            speaker for start, end, speaker in sys if start <= mid <= end
        )
        intervals.append((dur, ref_spk, sys_spk))

    # Pass 1: co-occurrence, used only for the optimal mapping.
    overlap: dict[str, dict[str, float]] = {}
    for dur, ref_spk, sys_spk in intervals:
        for r_speaker in ref_spk:
            row = overlap.setdefault(r_speaker, {})
            for s_speaker in sys_spk:
                row[s_speaker] = row.get(s_speaker, 0.0) + dur
    mapping = optimal_speaker_mapping(overlap)

    # Pass 2: the error terms, with the mapping fixed.
    scored_speaker_s = 0.0
    missed_s = 0.0
    false_alarm_s = 0.0
    speaker_confusion_s = 0.0
    for dur, ref_spk, sys_spk in intervals:
        n_ref, n_sys = len(ref_spk), len(sys_spk)
        scored_speaker_s += dur * n_ref
        missed_s += dur * max(n_ref - n_sys, 0)
        false_alarm_s += dur * max(n_sys - n_ref, 0)
        n_map = sum(1 for r_speaker in ref_spk if mapping.get(r_speaker) in sys_spk)
        speaker_confusion_s += dur * (min(n_ref, n_sys) - n_map)

    denominator = scored_speaker_s

    def _fraction(value: float) -> float:
        return value / denominator if denominator else 0.0

    return {
        "der": _fraction(missed_s + false_alarm_s + speaker_confusion_s),
        "missed": _fraction(missed_s),
        "false_alarm": _fraction(false_alarm_s),
        "speaker_confusion": _fraction(speaker_confusion_s),
        "collar": float(collar),
        "ref_speech_s": denominator,
        "scored_speaker_s": denominator,
        "missed_s": missed_s,
        "false_alarm_s": false_alarm_s,
        "speaker_confusion_s": speaker_confusion_s,
        "mapping": mapping,
        "scored_intervals": len(intervals),
    }


def relabel_sequence(
    ref_turns, sys_turns, mapping: dict[str, str]
) -> tuple[list[str], list[str | None]]:
    """Relabel system speakers through the inverse map, in time order."""
    inverse = {sys: ref for ref, sys in mapping.items()}
    ref_order = [speaker for _s, _e, speaker in _normalise_turns(ref_turns)]
    sys_order = [inverse.get(speaker) for _s, _e, speaker in _normalise_turns(sys_turns)]
    return ref_order, sys_order


# ---------------------------------------------------------------------------
# sherpa-onnx version (there is no __version__ and no dist metadata)
# ---------------------------------------------------------------------------

def sherpa_onnx_version() -> tuple[str | None, str | None]:
    """Return ``(version, source)`` for the installed sherpa-onnx.

    ``sherpa_onnx.__version__`` raises ``AttributeError`` and the wheel has no
    distribution metadata, so both are tried and then the version is read from
    the resolved nix store path, which is the only place it exists.
    """
    try:
        import sherpa_onnx
    except Exception:
        return None, None

    try:
        return str(sherpa_onnx.__version__), "attribute"
    except AttributeError:
        pass

    try:
        import importlib.metadata as metadata
        return metadata.version("sherpa-onnx"), "dist-metadata"
    except Exception:
        pass

    resolved = os.path.realpath(getattr(sherpa_onnx, "__file__", "") or "")
    match = re.search(r"sherpa[-_]onnx[-_](\d+\.\d+(?:\.\d+)?)", resolved)
    if match:
        return match.group(1), "nix-store-path"
    return None, None


# ---------------------------------------------------------------------------
# House-style metadata helpers (mirroring eval/score.py)
# ---------------------------------------------------------------------------

def _git_revision() -> tuple[str, bool]:
    try:
        rev = subprocess.run(
            ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", REPO_ROOT, "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return rev, bool(status)
    except Exception:
        return "", False


def _reproduce_command() -> str:
    env_prefix = " ".join(
        f"{key}={shlex.quote(os.environ[key])}"
        for key in _RECORDED_ENV_VARS if key in os.environ
    )
    argv = ["python", os.path.relpath(__file__, REPO_ROOT)] + sys.argv[1:]
    return f"{env_prefix} {' '.join(shlex.quote(a) for a in argv)}".strip()


def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score the committed diarization fixture with DER.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE, help="16 kHz mono WAV")
    parser.add_argument("--rttm", default=DEFAULT_RTTM, help="reference RTTM")
    parser.add_argument("--json", dest="json_out", default=DEFAULT_JSON,
                        help="write the results JSON here")
    parser.add_argument("--collar", type=float, default=COLLAR_S,
                        help="no-score collar around reference boundaries (s)")
    parser.add_argument("--speakers", default="2",
                        help="known speaker count, or 'auto' (default: 2)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="clustering threshold (only used when auto)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, SRC_DIR)
    from voice_transcriber import diarize as diarize_mod  # noqa: E402

    # Availability is the backend's own fail-safe: absent graphs (or no
    # sherpa_onnx) must skip, never fail. An empty turn list is a legitimate
    # single-speaker result, so it cannot be used to detect "unavailable".
    backend = diarize_mod.load_backend("sherpa-onnx")
    if backend is None or not backend.available():
        eprint("[diarization] sherpa-onnx backend unavailable (missing sherpa_onnx "
               "or diarization graphs) -- skipping; install the 'diarization' "
               "model to run this eval")
        return 0

    num_speakers = diarize_mod.parse_speakers(args.speakers)
    threshold = (
        diarize_mod.DEFAULT_THRESHOLD if args.threshold is None else args.threshold
    )

    import numpy as np  # noqa: E402
    import soundfile as sf  # noqa: E402

    audio, sample_rate = sf.read(args.fixture, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    duration = len(audio) / float(sample_rate)

    ref_turns = load_rttm(args.rttm)
    if not ref_turns:
        eprint(f"[diarization] no SPEAKER lines in {args.rttm}")
        return 2

    progress_calls = [0]

    def progress(_done: int, _total: int) -> None:
        progress_calls[0] += 1

    turns = diarize_mod.diarize(
        audio,
        int(sample_rate),
        num_speakers=num_speakers,
        threshold=float(threshold),
        progress=progress,
    )
    sys_turns = [(turn.start, turn.end, str(turn.speaker)) for turn in turns]

    metrics = compute_der(
        ref_turns, sys_turns, collar=args.collar, duration=duration,
    )
    ref_order, sys_order = relabel_sequence(ref_turns, sys_turns, metrics["mapping"])

    turn_count = len(sys_turns)
    ordering_ok = sys_order == ref_order
    der_ok = metrics["der"] <= DER_GATE
    components_ok = (
        metrics["missed"] < COMPONENT_GATE
        and metrics["false_alarm"] < COMPONENT_GATE
    )
    passed = (
        turn_count == EXPECTED_TURNS and ordering_ok and der_ok and components_ok
    )

    version, version_source = sherpa_onnx_version()
    rev, dirty = _git_revision()

    print("=" * 72)
    print("Voice Transcriber diarization eval (synthetic regression smoke gate)")
    print("=" * 72)
    print(f"fixture        : {os.path.relpath(args.fixture, REPO_ROOT)} "
          f"({duration:.3f} s, {sample_rate} Hz)")
    print(f"reference      : {os.path.relpath(args.rttm, REPO_ROOT)} "
          f"({len(ref_turns)} turns)")
    print(f"backend        : sherpa-onnx {version or '<unknown>'} "
          f"({version_source or 'unknown source'})")
    print(f"speakers       : {args.speakers} -> num_speakers={num_speakers}")
    print(f"collar         : {args.collar} s")
    print(f"detected turns : {turn_count}  speakers={len({t[2] for t in sys_turns})} "
          f"progress_calls={progress_calls[0]}")
    for start, end, speaker in sys_turns:
        print(f"  sys {speaker}: {start:.3f} - {end:.3f}  ({end - start:.3f}s)")
    print(f"mapping        : {metrics['mapping']}")
    print(f"ordering       : ref={ref_order} sys={sys_order}")
    print("-" * 72)
    print(f"DER            : {metrics['der']:.4f}  (gate <= {DER_GATE})")
    print(f"  missed       : {metrics['missed']:.4f}  ({metrics['missed_s']:.3f} s, "
          f"gate < {COMPONENT_GATE})")
    print(f"  false_alarm  : {metrics['false_alarm']:.4f}  "
          f"({metrics['false_alarm_s']:.3f} s, gate < {COMPONENT_GATE})")
    print(f"  confusion    : {metrics['speaker_confusion']:.4f}  "
          f"({metrics['speaker_confusion_s']:.3f} s)")
    print(f"ref_speech_s   : {metrics['ref_speech_s']:.3f}")
    print(f"turns == {EXPECTED_TURNS}    : {turn_count == EXPECTED_TURNS}")
    print(f"ordering A,B,A,B: {ordering_ok}")
    print("-" * 72)
    print("RESULT         : " + ("PASS" if passed else "FAIL"))

    # Per-turn view: the system turns annotated with the reference speaker the
    # mapping assigns to them, so a human can see the A-B-A-B pattern.
    inverse = {sys_spk: ref_spk for ref_spk, sys_spk in metrics["mapping"].items()}
    results = [{
        "id": os.path.splitext(os.path.basename(args.fixture))[0],
        "ref_turns": [
            {"speaker": speaker, "start": round(start, 3), "end": round(end, 3)}
            for start, end, speaker in ref_turns
        ],
        "sys_turns": [
            {
                "speaker": speaker,
                "mapped_speaker": inverse.get(speaker),
                "start": round(start, 3),
                "end": round(end, 3),
            }
            for start, end, speaker in sys_turns
        ],
        "turn_count": turn_count,
        "ref_order": ref_order,
        "sys_order": sys_order,
        "ordering_ok": ordering_ok,
        **{
            key: metrics[key]
            for key in ("der", "missed", "false_alarm", "speaker_confusion")
        },
    }]

    if args.json_out:
        payload = {
            "meta": {
                "kind": "diarization",
                "fixture": os.path.relpath(args.fixture, REPO_ROOT),
                "rttm": os.path.relpath(args.rttm, REPO_ROOT),
                "backend": "sherpa-onnx",
                "sherpa_onnx_version": version,
                "sherpa_onnx_version_source": version_source,
                "num_speakers": num_speakers,
                "speakers_setting": args.speakers,
                "threshold": float(threshold),
                "collar": args.collar,
                "der": metrics["der"],
                "missed": metrics["missed"],
                "false_alarm": metrics["false_alarm"],
                "speaker_confusion": metrics["speaker_confusion"],
                "ref_speech_s": metrics["ref_speech_s"],
                "scored_speaker_s": metrics["scored_speaker_s"],
                "missed_s": metrics["missed_s"],
                "false_alarm_s": metrics["false_alarm_s"],
                "speaker_confusion_s": metrics["speaker_confusion_s"],
                "scored_intervals": metrics["scored_intervals"],
                "mapping": metrics["mapping"],
                "turn_count": turn_count,
                "expected_turns": EXPECTED_TURNS,
                "ordering": sys_order,
                "expected_order": EXPECTED_ORDER,
                "ordering_ok": ordering_ok,
                "der_gate": DER_GATE,
                "component_gate": COMPONENT_GATE,
                "passed": passed,
                "progress_calls": progress_calls[0],
                "git_rev": rev,
                "git_dirty": dirty,
                "invocation": ["python", os.path.relpath(__file__, REPO_ROOT)]
                              + sys.argv[1:],
                "env": {k: os.environ[k] for k in _RECORDED_ENV_VARS if k in os.environ},
                "reproduce": _reproduce_command(),
            },
            "results": results,
        }
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        eprint(f"[diarization] wrote {args.json_out}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Score Voice Transcriber accuracy on eval/manifest.jsonl using the REAL pipeline.

Pipeline under test
-------------------
    audio blocks -> src/micro_batcher.StreamingMicroBatcher (energy VAD + overlap
    stitching) -> src/transcribe2 (Cohere ASR) -> post_processor.clean_speech_transcription(skip_slm=True)

Metrics
-------
  * WER  : corpus word error rate (sum Levenshtein word edits / sum ref words)
  * CER  : corpus character error rate (self-implemented Levenshtein; no deps)
  * exact: fraction of clips whose normalised hypothesis == normalised reference
  * silence hallucination rate: fraction of "silence" clips with non-empty output

Normalisation for scoring: lowercase, every non [a-z0-9] character becomes a
space, whitespace collapsed. This splits identifiers/paths/versions/IPs into
tokens on both sides (server_setup -> "server setup", 192.168.1.10 -> "192 168
1 10") so WER is not dominated by punctuation spelling conventions.

Usage
-----
  PYTHONPATH=$PWD/src $PY eval/score.py                 # all 154 clips
  PYTHONPATH=$PWD/src $PY eval/score.py --limit 3       # smoke test
  PYTHONPATH=$PWD/src $PY eval/score.py --slice clean --slice technical
  PYTHONPATH=$PWD/src $PY eval/score.py --no-int8       # A/B int8 vs fp32
  PYTHONPATH=$PWD/src $PY eval/score.py --json eval/results.json

The app's real config is read from config/config.yaml (number_digits).
VT_INT8_DYNAMIC is honoured; --int8/--no-int8 override it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time

os.environ.setdefault("VT_MODEL_BACKEND", "cohere")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_DIR)

TARGET_SR = 16000


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Scoring primitives (self-implemented, no extra deps)
# ---------------------------------------------------------------------------
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_DISFLUENCY_FILLERS_RE = re.compile(r"\b(uh|um|er|ah|mm)\b", re.IGNORECASE)
_STUTTER_DUP_RE = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)

TECHNICAL_IDENTIFIERS = [
    "systemctl", "journalctl", "nixos-rebuild", "NixOS", "Docker", "Kubernetes",
    "Ansible", "Terraform", "GitLab", "MySQL", "PostgreSQL", "Veeam", "Redis",
    "Postfix", "Dovecot", "SpamAssassin", "WSL", "x86_64", "0xDEADBEEF", "Altium",
    "PCB", "PLC", "UART", "SPI", "I2C", "GPIO", "Modbus", "Pololu", "carrierCode",
    "exitHook", "server_setup", "work_notes", "merge_requests", "VLAN", "LDAP",
    "DHCP", "DNS", "SSH", "HTTPS", "IMAP", "TCP", "CI", "192.168.1.10", "10.0.0.0/24",
    "6.5.12", "1.18.4", "configuration.nix"
]


def normalize(text: str, normalize_numbers: bool = True) -> str:
    if not text:
        return ""
    if normalize_numbers:
        # Strip punctuation first so number words parse cleanly without phrase splits
        t = re.sub(r"[,.?!;:]", " ", text)
        try:
            from post_processor import convert_number_words_to_digits, set_number_digits_enabled
            set_number_digits_enabled(True)
            t = convert_number_words_to_digits(" ".join(t.split()).lower())
            t = re.sub(r"(\d),(\d)", r"\1\2", t)
            text = t
        except Exception:
            pass
    return _NORMALIZE_RE.sub(" ", text.lower()).strip()


def normalize_clean(text: str, normalize_numbers: bool = True) -> str:
    """Normalize text while removing conversational disfluencies (fillers & stutters)."""
    if not text:
        return ""
    t = _DISFLUENCY_FILLERS_RE.sub(" ", text)
    while True:
        nt = _STUTTER_DUP_RE.sub(r"\1", t)
        if nt == t:
            break
        t = nt
    return normalize(t, normalize_numbers=normalize_numbers)


def check_identifier_fidelity(ref: str, hyp: str) -> tuple[int, int]:
    """Check how many technical identifiers present in ref appear exactly in hyp."""
    hits, total = 0, 0
    for term in TECHNICAL_IDENTIFIERS:
        pat = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
        if pat.search(ref):
            total += 1
            if pat.search(hyp):
                hits += 1
    return hits, total


def _levenshtein(a, b) -> int:
    """Classic DP edit distance between two sequences (lists or strs)."""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        cur[0] = i
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev, cur = cur, prev
    return prev[m]


def word_edits(ref: str, hyp: str, normalize_numbers: bool = True) -> tuple[int, int]:
    r = normalize(ref, normalize_numbers=normalize_numbers).split()
    h = normalize(hyp, normalize_numbers=normalize_numbers).split()
    return _levenshtein(r, h), len(r)


def word_edits_clean(ref: str, hyp: str, normalize_numbers: bool = True) -> tuple[int, int]:
    r = normalize_clean(ref, normalize_numbers=normalize_numbers).split()
    h = normalize_clean(hyp, normalize_numbers=normalize_numbers).split()
    return _levenshtein(r, h), len(r)


def char_edits(ref: str, hyp: str, normalize_numbers: bool = True) -> tuple[int, int]:
    r = normalize(ref, normalize_numbers=normalize_numbers).replace(" ", "")
    h = normalize(hyp, normalize_numbers=normalize_numbers).replace(" ", "")
    return _levenshtein(r, h), len(r)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_app_config() -> dict:
    path = os.path.join(REPO_ROOT, "config", "config.yaml")
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        eprint(f"[score] could not read {path}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Transcription via the real pipeline
# ---------------------------------------------------------------------------
def transcribe_clip(audio, blocks_ms: int) -> str:
    import numpy as np
    from micro_batcher import StreamingMicroBatcher

    audio = np.asarray(audio, dtype=np.float32).ravel()
    batcher = StreamingMicroBatcher(sample_rate=TARGET_SR)
    batcher.start()
    block = max(1, int(blocks_ms / 1000.0 * TARGET_SR))
    for i in range(0, len(audio), block):
        batcher.feed_audio(audio[i:i + block])
    return batcher.finish_and_get_text(skip_slm=True)


def load_audio(path: str):
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = np.ascontiguousarray(data, dtype=np.float32)
    if sr != TARGET_SR:
        import librosa
        data = librosa.resample(data, orig_sr=sr, target_sr=TARGET_SR)
    return data


def select_slice(ref: str, hyp: str) -> str:
    return "silence" if ref.strip() == "" else "speech"


def main():
    ap = argparse.ArgumentParser(description="Score Voice Transcriber with WER/CER.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=os.path.join(EVAL_DIR, "manifest.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="only first N clips")
    ap.add_argument("--slice", action="append", default=[], help="restrict to slice (repeatable)")
    ap.add_argument("--ids", default="", help="comma-separated clip ids")
    ap.add_argument("--blocks-ms", type=int, default=100, help="audio feed block size (ms)")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--int8", dest="int8", action="store_true", default=None,
                    help="force VT_INT8_DYNAMIC=1")
    ap.add_argument("--no-int8", dest="int8", action="store_false",
                    help="force VT_INT8_DYNAMIC=0 (fp32 A/B)")
    ap.add_argument("--number-digits", dest="num_digits", action="store_true", default=None,
                    help="override config number_digits")
    ap.add_argument("--no-number-digits", dest="num_digits", action="store_false")
    ap.add_argument("--raw-numbers", dest="norm_numbers", action="store_false", default=True,
                    help="disable spoken-number normalization in scoring")
    ap.add_argument("--json", dest="json_out", default="", help="write full results JSON")
    ap.add_argument("--max-offenders", type=int, default=15)
    ap.add_argument("--verbose", action="store_true", help="do not silence model/pipeline stdout")
    args = ap.parse_args()

    # ---- apply int8 choice before importing the backend ----
    if args.int8 is True:
        os.environ["VT_INT8_DYNAMIC"] = "1"
    elif args.int8 is False:
        os.environ["VT_INT8_DYNAMIC"] = "0"

    # ---- app config -> number_digits ----
    cfg = load_app_config()
    num_digits = cfg.get("number_digits", False)
    if args.num_digits is not None:
        num_digits = args.num_digits
    from post_processor import set_number_digits_enabled  # noqa: E402
    set_number_digits_enabled(bool(num_digits))

    # ---- read + filter manifest ----
    entries = []
    with open(args.manifest) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    if args.slice:
        wanted = set(args.slice)
        entries = [e for e in entries if e["slice"] in wanted]
    if args.ids:
        wanted = set(x.strip() for x in args.ids.split(",") if x.strip())
        entries = [e for e in entries if e["id"] in wanted]
    if args.limit:
        entries = entries[: args.limit]

    if not entries:
        eprint("[score] no clips selected")
        return 2

    from collections import Counter, defaultdict
    counts = Counter(e["slice"] for e in entries)

    eprint("=" * 72)
    eprint("Voice Transcriber accuracy evaluation")
    eprint("=" * 72)
    eprint(f"clips        : {len(entries)} ({', '.join(f'{k}={v}' for k, v in sorted(counts.items()))})")
    eprint(f"backend      : cohere  (VT_INT8_DYNAMIC={os.environ.get('VT_INT8_DYNAMIC', '0')})")
    eprint(f"number_digits: {bool(num_digits)} (config value: {cfg.get('number_digits', False)})")
    eprint(f"feed block   : {args.blocks_ms} ms")

    # ---- silence pipeline stdout while loading/running model ----
    devnull = open(os.devnull, "w")
    redirect = contextlib.nullcontext() if args.verbose else contextlib.redirect_stdout(devnull)

    with redirect:
        t0 = time.perf_counter()
        import transcribe2
        transcribe2.get_model(device=args.device)
        eprint(f"model loaded  : {time.perf_counter() - t0:.1f} s")

    results = []
    t_start = time.perf_counter()
    with redirect:
        for i, e in enumerate(entries, 1):
            path = os.path.join(EVAL_DIR, e["path"])
            audio = load_audio(path)
            t1 = time.perf_counter()
            hyp = transcribe_clip(audio, args.blocks_ms)
            dt = time.perf_counter() - t1
            we, wr = word_edits(e["ref"], hyp, normalize_numbers=args.norm_numbers)
            ce, cr = char_edits(e["ref"], hyp, normalize_numbers=args.norm_numbers)
            c_we, c_wr = word_edits_clean(e["ref"], hyp, normalize_numbers=args.norm_numbers)
            id_hits, id_tot = check_identifier_fidelity(e["ref"], hyp)
            results.append({
                "id": e["id"], "slice": e["slice"], "ref": e["ref"], "hyp": hyp,
                "duration_s": e["duration_s"], "latency_s": round(dt, 3),
                "word_edits": we, "ref_words": wr, "char_edits": ce, "ref_chars": cr,
                "clean_word_edits": c_we, "clean_ref_words": c_wr,
                "id_hits": id_hits, "id_total": id_tot,
                "exact": normalize(e["ref"], normalize_numbers=args.norm_numbers) == normalize(hyp, normalize_numbers=args.norm_numbers),
                "empty": hyp.strip() == "",
                "wer": (we / wr) if wr else (0.0 if hyp.strip() == "" else 1.0),
                "clean_wer": (c_we / c_wr) if c_wr else (0.0 if hyp.strip() == "" else 1.0),
            })
            if i % 10 == 0 or i == len(entries):
                eprint(f"  scored {i}/{len(entries)} ({e['slice']}:{e['id']})")

    # ---- aggregate ----
    per_slice = defaultdict(lambda: {"n": 0, "we": 0, "wr": 0, "ce": 0, "cr": 0,
                                     "clean_we": 0, "clean_wr": 0,
                                     "id_hits": 0, "id_total": 0,
                                     "exact": 0, "empty": 0, "latency": 0.0, "dur": 0.0})
    for r in results:
        s = per_slice[r["slice"]]
        s["n"] += 1
        s["we"] += r["word_edits"]; s["wr"] += r["ref_words"]
        s["ce"] += r["char_edits"]; s["cr"] += r["ref_chars"]
        s["clean_we"] += r["clean_word_edits"]; s["clean_wr"] += r["clean_ref_words"]
        s["id_hits"] += r["id_hits"]; s["id_total"] += r["id_total"]
        s["exact"] += 1 if r["exact"] else 0
        s["empty"] += 1 if r["empty"] else 0
        s["latency"] += r["latency_s"]; s["dur"] += r["duration_s"]

    # speech slices exclude silence from WER (empty ref)
    speech = [r for r in results if r["slice"] != "silence"]
    tot_we = sum(r["word_edits"] for r in speech)
    tot_wr = sum(r["ref_words"] for r in speech)
    tot_ce = sum(r["char_edits"] for r in speech)
    tot_cr = sum(r["ref_chars"] for r in speech)
    tot_c_we = sum(r["clean_word_edits"] for r in speech)
    tot_c_wr = sum(r["clean_ref_words"] for r in speech)
    tot_id_hits = sum(r["id_hits"] for r in speech)
    tot_id_total = sum(r["id_total"] for r in speech)

    overall_wer = (tot_we / tot_wr) if tot_wr else 0.0
    overall_clean_wer = (tot_c_we / tot_c_wr) if tot_c_wr else 0.0
    overall_cer = (tot_ce / tot_cr) if tot_cr else 0.0
    exact_rate = sum(1 for r in speech if r["exact"]) / len(speech) if speech else 0.0
    id_fid_rate = (tot_id_hits / tot_id_total) if tot_id_total else 0.0

    silence = [r for r in results if r["slice"] == "silence"]
    halluc = sum(1 for r in silence if not r["empty"]) / len(silence) if silence else 0.0

    def fmt_pct(x):
        return f"{100.0 * x:6.2f}%"

    print()
    print("=" * 96)
    print(f"{'slice':<18}{'n':>4}  {'WER':>8}  {'cleanWER':>9}  {'CER':>8}  {'exact':>8}  {'id_fid':>8}  {'empty':>6}  {'mean_lat':>9}")
    print("-" * 96)
    rows = []
    for sl in sorted(per_slice):
        s = per_slice[sl]
        if sl == "silence":
            wer_s = clean_wer_s = cer_s = exact_s = id_fid_s = "     n/a"
            empty_s = fmt_pct(s["empty"] / s["n"]).strip()
        else:
            wer_s = fmt_pct(s["we"] / s["wr"]) if s["wr"] else "     n/a"
            clean_wer_s = fmt_pct(s["clean_we"] / s["clean_wr"]) if s["clean_wr"] else "     n/a"
            cer_s = fmt_pct(s["ce"] / s["cr"]) if s["cr"] else "     n/a"
            exact_s = fmt_pct(s["exact"] / s["n"])
            id_fid_s = fmt_pct(s["id_hits"] / s["id_total"]) if s["id_total"] else "     n/a"
            empty_s = fmt_pct(s["empty"] / s["n"]).strip()
        mean_lat = s["latency"] / s["n"]
        rows.append((sl, s["n"], wer_s, clean_wer_s, cer_s, exact_s, id_fid_s, empty_s, mean_lat))
        print(f"{sl:<18}{s['n']:>4}  {wer_s:>8}  {clean_wer_s:>9}  {cer_s:>8}  {exact_s:>8}  {id_fid_s:>8}  {empty_s:>6}  {mean_lat:>8.2f}s")
    print("-" * 96)
    print(f"{'OVERALL (speech)':<18}{len(speech):>4}  {fmt_pct(overall_wer):>8}  {fmt_pct(overall_clean_wer):>9}  {fmt_pct(overall_cer):>8}  {fmt_pct(exact_rate):>8}  {fmt_pct(id_fid_rate):>8}")
    print(f"silence hallucination rate: {fmt_pct(halluc).strip()}  ({sum(1 for r in silence if not r['empty'])}/{len(silence)} non-empty)")
    if tot_id_total:
        print(f"technical identifier fidelity: {fmt_pct(id_fid_rate).strip()} ({tot_id_hits}/{tot_id_total} matched exactly)")
    print(f"total wall time: {time.perf_counter() - t_start:.1f}s for {len(results)} clips")

    # ---- worst offenders ----
    offenders = sorted(speech, key=lambda r: (r["word_edits"], r["wer"]), reverse=True)
    n_off = min(args.max_offenders, len(offenders))
    if n_off:
        print()
        print("=" * 84)
        print(f"WORST OFFENDERS (top {n_off} by word edits)")
        print("=" * 84)
        for r in offenders[:n_off]:
            print(f"\n[{r['slice']}:{r['id']}] WER={r['wer']:.3f} (clean={r['clean_wer']:.3f}) edits={r['word_edits']}/{r['ref_words']}  dur={r['duration_s']}s")
            print(f"  REF: {r['ref'][:300]}")
            print(f"  HYP: {r['hyp'][:300]}")

    # ---- JSON export ----
    if args.json_out:
        payload = {
            "meta": {
                "n_clips": len(results),
                "slices": dict(counts),
                "int8_dynamic": os.environ.get("VT_INT8_DYNAMIC", "0"),
                "number_digits": bool(num_digits),
                "norm_numbers": args.norm_numbers,
                "blocks_ms": args.blocks_ms,
                "overall_wer": overall_wer,
                "overall_clean_wer": overall_clean_wer,
                "overall_cer": overall_cer,
                "exact_rate": exact_rate,
                "id_fidelity_rate": id_fid_rate,
                "id_hits": tot_id_hits,
                "id_total": tot_id_total,
                "silence_hallucination_rate": halluc,
            },
            "per_slice": {k: dict(v) for k, v in per_slice.items()},
            "results": results,
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        eprint(f"[score] wrote {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

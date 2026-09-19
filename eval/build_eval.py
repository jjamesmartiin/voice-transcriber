#!/usr/bin/env python3
"""
Build a REAL accuracy evaluation set for Voice Transcriber (English ASR).

Produces >=135 mono, 16 kHz, float32 WAV clips under eval/data/ plus
eval/manifest.jsonl with fields:

    {"id", "path", "ref", "slice", "source", "duration_s"}

Slices
------
  clean            real read speech (LibriSpeech test-clean), 3-10 s
  accented         non-US English read/meeting speech (AMI IHM), 3-10 s
  noisy            clean/accented/technical audio + generated noise @ 5/10/20 dB SNR
  long             15-40 s clips built by concatenating consecutive LibriSpeech
                   utterances from one speaker (ref = concatenated refs)
  silence          pure silence / very low-level generated noise, ref = "" (hallucination probe)
  technical        gTTS-synthesised generic infra/DevOps/hardware-AI sentences
  technical_noisy  technical clips + generated noise @ 10/20 dB SNR

Reproducibility
---------------
The audio is git-ignored. The *selection* of network samples is pinned in
eval/pinned_ids.json (written on the first successful build and reused on
later builds). Synthetic slices (technical, silence, noise mixes) are fully
deterministic from RNG_SEED. Delete eval/pinned_ids.json to re-select the
first N qualifying samples deterministically from the streaming sources.

NOTE: technical sentences are generic technical prose written for this eval.
They were NOT copied from any private notes; vocabulary was used only as a
topic list.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import wave

import numpy as np

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EVAL_DIR, "data")
MANIFEST_PATH = os.path.join(EVAL_DIR, "manifest.jsonl")
PINNED_PATH = os.path.join(EVAL_DIR, "pinned_ids.json")

TARGET_SR = 16000
RNG_SEED = 20260918

COUNTS = {
    "clean": 44,
    "accented": 22,
    "noisy": 24,
    "long": 12,
    "silence": 12,
    "technical": 28,
    "technical_noisy": 12,
}

CLEAN_MIN_S, CLEAN_MAX_S = 3.0, 10.0
LONG_MIN_S, LONG_MAX_S = 15.0, 40.0

# Source datasets. AMI's config "ihm" (individual headset mic) is available
# without auth; FLEURS only ships en_us in the public Hub config (en_gb/au/in
# do not exist), so AMI is used for non-US English.
LIBRISPEECH = ("openslr/librispeech_asr", "clean")
AMI = ("edinburghcstr/ami", "ihm")


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[build_eval] {msg}", flush=True)


def load_pinned():
    if os.path.exists(PINNED_PATH):
        try:
            with open(PINNED_PATH, "r") as f:
                return json.load(f)
        except Exception as e:  # pragma: no cover
            log(f"could not read pinned ids ({e}); will re-select")
    return {}


def save_pinned(pinned: dict) -> None:
    with open(PINNED_PATH, "w") as f:
        json.dump(pinned, f, indent=2)
    log(f"wrote {PINNED_PATH}")


def stream_source(name: str, config: str | None, split: str):
    """Stream a HF dataset with audio decoding disabled (raw bytes)."""
    from datasets import Audio, load_dataset

    if config:
        ds = load_dataset(name, config, split=split, streaming=True)
    else:
        ds = load_dataset(name, split=split, streaming=True)
    return ds.cast_column("audio", Audio(decode=False))


def decode_example_audio(ex) -> np.ndarray:
    """Decode an undecoded HF Audio cell to mono float32 at TARGET_SR."""
    import io as _io

    import soundfile as sf

    b = ex["audio"].get("bytes")
    sr = ex["audio"].get("sampling_rate") or TARGET_SR
    if b is not None:
        data, file_sr = sf.read(_io.BytesIO(b), dtype="float32", always_2d=False)
        sr = file_sr
    else:
        data, sr = sf.read(ex["audio"]["path"], dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = np.asarray(data, dtype=np.float32).ravel()
    if sr != TARGET_SR:
        import librosa

        data = librosa.resample(data, orig_sr=sr, target_sr=TARGET_SR)
    return np.ascontiguousarray(data, dtype=np.float32)


def write_wav(path: str, audio: np.ndarray) -> float:
    """Write mono float32 PCM_16 WAV; return duration in seconds."""
    import soundfile as sf

    os.makedirs(os.path.dirname(path), exist_ok=True)
    audio = np.asarray(audio, dtype=np.float32).ravel()
    if audio.size and float(np.max(np.abs(audio))) > 0.999:
        audio = audio / float(np.max(np.abs(audio))) * 0.97
    sf.write(path, audio, TARGET_SR, subtype="FLOAT")
    return float(len(audio)) / TARGET_SR


def rel(path: str) -> str:
    return os.path.relpath(path, EVAL_DIR)


# ---------------------------------------------------------------------------
# Noise generators
# ---------------------------------------------------------------------------
def _white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n).astype(np.float32)


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise via spectral shaping (Voss-like 1/f amplitude)."""
    w = _white(n, rng)
    spec = np.fft.rfft(w)
    freqs = np.fft.rfftfreq(n, d=1.0 / TARGET_SR)
    scale = np.ones_like(freqs)
    scale[1:] = 1.0 / np.sqrt(freqs[1:])
    out = np.fft.irfft(spec * scale, n=n).astype(np.float32)
    return out


def _babble(n: int, rng: np.random.Generator, n_voices: int = 6) -> np.ndarray:
    """Babble-ish: sum of several band-limited, slow-AM noise sources."""
    out = np.zeros(n, dtype=np.float32)
    for i in range(n_voices):
        base = rng.standard_normal(n).astype(np.float32)
        # crude band-limit via a short moving average (low-pass) + differencing
        k = int(rng.integers(20, 120))
        kern = np.ones(k, dtype=np.float32) / k
        base = np.convolve(base, kern, mode="same")
        # slow amplitude modulation (speech-like syllable rate)
        rate = rng.uniform(2.0, 5.0)
        t = np.arange(n, dtype=np.float32) / TARGET_SR
        am = 0.55 + 0.45 * np.sin(2 * np.pi * rate * t + rng.uniform(0, 6.28))
        out += base * am
    return out / max(1, n_voices)


def make_noise(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    if kind == "white":
        return _white(n, rng)
    if kind == "pink":
        return _pink(n, rng)
    return _babble(n, rng)


def mix_at_snr(speech: np.ndarray, snr_db: float, rng: np.random.Generator, kind: str) -> np.ndarray:
    """Mix generated noise into speech at the requested SNR (dB)."""
    speech = np.asarray(speech, dtype=np.float32).ravel()
    sp = float(np.mean(speech.astype(np.float64) ** 2)) + 1e-12
    noise = make_noise(kind, len(speech), rng)
    npr = float(np.mean(noise.astype(np.float64) ** 2)) + 1e-12
    target_np = sp / (10.0 ** (snr_db / 10.0))
    noise = noise * np.sqrt(target_np / npr)
    mixed = speech + noise.astype(np.float32)
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.99:
        mixed = mixed / peak * 0.97
    return mixed.astype(np.float32)


# ---------------------------------------------------------------------------
# Slice builders
# ---------------------------------------------------------------------------
def collect_stream(name, config, split, target_ids, accept, limit, scan_cap=6000):
    """Stream a source and return (selected, scanned).

    If target_ids is non-empty, select exactly those ids. Otherwise select the
    first `limit` examples passing `accept`.
    """
    from datasets import load_dataset, Audio  # noqa: F401

    remaining = set(target_ids) if target_ids else None
    selected = []
    scanned = 0
    for ex in stream_source(name, config, split):
        scanned += 1
        if scanned > scan_cap:
            log(f"scan cap {scan_cap} reached for {name}")
            break
        if remaining is not None:
            key = ex.get("id") or ex.get("audio_id")
            if key in remaining:
                selected.append(ex)
                remaining.discard(key)
                if not remaining:
                    break
        else:
            ok, _ = accept(ex)
            if ok:
                selected.append(ex)
                if len(selected) >= limit:
                    break
    return selected, scanned


def build_clean(pinned):
    log("=== slice: clean (LibriSpeech test-clean) ===")
    target = pinned.get("clean", [])
    import soundfile as sf

    results = []
    dir_ = os.path.join(DATA_DIR, "clean")

    def accept(ex):
        try:
            a = decode_example_audio(ex)
        except Exception:
            return False, None
        d = len(a) / TARGET_SR
        return (CLEAN_MIN_S <= d <= CLEAN_MAX_S), a

    if target:
        # streaming twice: once to find, decoding each candidate to filter dur
        from datasets import load_dataset, Audio  # noqa
        remaining = set(target)
        for ex in stream_source(*LIBRISPEECH, split="test"):
            key = ex.get("id")
            if key not in remaining:
                continue
            a = decode_example_audio(ex)
            results.append((key, ex.get("speaker_id"), ex.get("chapter_id"), ex["text"].strip(), a))
            remaining.discard(key)
            if not remaining:
                break
    else:
        from datasets import load_dataset, Audio  # noqa
        for ex in stream_source(*LIBRISPEECH, split="test"):
            ok, a = accept(ex)
            if ok:
                key = ex.get("id")
                results.append((key, ex.get("speaker_id"), ex.get("chapter_id"), ex["text"].strip(), a))
                if len(results) >= COUNTS["clean"]:
                    break

    manifest = []
    for i, (key, spk, chap, text, a) in enumerate(results):
        cid = f"clean-{i:04d}"
        path = os.path.join(dir_, f"{cid}.wav")
        dur = write_wav(path, a)
        manifest.append({
            "id": cid, "path": rel(path), "ref": text, "slice": "clean",
            "source": f"openslr/librispeech_asr:clean:test:{key}", "duration_s": round(dur, 3),
        })
    log(f"clean: {len(manifest)} clips")
    return manifest, [r[0] for r in results]


def build_accented(pinned):
    log("=== slice: accented (AMI IHM, non-US English) ===")
    target = pinned.get("accented", [])
    results = []
    dir_ = os.path.join(DATA_DIR, "accented")

    if target:
        remaining = set(target)
        for ex in stream_source(*AMI, split="train"):
            key = f"{ex.get('meeting_id')}:{ex.get('audio_id')}"
            if key not in remaining:
                continue
            a = decode_example_audio(ex)
            results.append((key, ex.get("speaker_id"), ex["text"].strip(), a))
            remaining.discard(key)
            if not remaining:
                break
    else:
        for ex in stream_source(*AMI, split="train"):
            try:
                a = decode_example_audio(ex)
            except Exception:
                continue
            d = len(a) / TARGET_SR
            if not (CLEAN_MIN_S <= d <= CLEAN_MAX_S):
                continue
            key = f"{ex.get('meeting_id')}:{ex.get('audio_id')}"
            text = ex["text"].strip()
            if not text:
                continue
            results.append((key, ex.get("speaker_id"), text, a))
            if len(results) >= COUNTS["accented"]:
                break

    manifest = []
    for i, (key, spk, text, a) in enumerate(results):
        cid = f"accented-{i:04d}"
        path = os.path.join(dir_, f"{cid}.wav")
        dur = write_wav(path, a)
        manifest.append({
            "id": cid, "path": rel(path), "ref": text, "slice": "accented",
            "source": f"edinburghcstr/ami:ihm:train:{key}:spk={spk}", "duration_s": round(dur, 3),
        })
    log(f"accented: {len(manifest)} clips")
    return manifest, [r[0] for r in results]


def _concat_segments(segs):
    gap = np.zeros(int(0.35 * TARGET_SR), dtype=np.float32)
    parts = []
    for j, s in enumerate(segs):
        if j:
            parts.append(gap)
        parts.append(s)
    return np.concatenate(parts)


def build_long(pinned):
    """Concatenate consecutive LibriSpeech utterances from one chapter to 15-40 s."""
    log("=== slice: long (LibriSpeech concatenations) ===")
    target_groups = pinned.get("long", [])
    dir_ = os.path.join(DATA_DIR, "long")
    manifest = []

    if target_groups:
        want = {t for grp in target_groups for t in grp}
        info = {}
        for ex in stream_source(*LIBRISPEECH, split="test"):
            key = ex.get("id")
            if key in want:
                info[key] = (ex["text"].strip(), ex.get("chapter_id"), ex.get("speaker_id"), decode_example_audio(ex))
                if len(info) >= len(want):
                    break
        for i, grp in enumerate(target_groups):
            if not all(k in info for k in grp):
                log(f"  long group {i} incomplete, skipping")
                continue
            texts = [info[k][0] for k in grp]
            segs = [info[k][3] for k in grp]
            audio = _concat_segments(segs)
            cid = f"long-{i:04d}"
            path = os.path.join(dir_, f"{cid}.wav")
            dur = write_wav(path, audio)
            manifest.append({
                "id": cid, "path": rel(path), "ref": " ".join(texts), "slice": "long",
                "source": f"openslr/librispeech_asr:clean:test:concat:{'+'.join(grp)}",
                "duration_s": round(dur, 3),
            })
        log(f"long: {len(manifest)} clips")
        return manifest, target_groups

    # First run: stream utterances and greedily grow a consecutive run per chapter.
    chapter_runs = {}
    for ex in stream_source(*LIBRISPEECH, split="test"):
        chap = ex.get("chapter_id")
        a = decode_example_audio(ex)
        d = len(a) / TARGET_SR
        run = chapter_runs.get(chap)
        if run is None:
            run = {"ids": [], "texts": [], "audio": [], "spk": ex.get("speaker_id"), "total": 0.0}
            chapter_runs[chap] = run
        run["ids"].append(ex.get("id"))
        run["texts"].append(ex["text"].strip())
        run["audio"].append(a)
        run["total"] += d
        if run["total"] >= LONG_MIN_S:
            i = len(manifest)
            cid = f"long-{i:04d}"
            path = os.path.join(dir_, f"{cid}.wav")
            dur = write_wav(path, _concat_segments(run["audio"]))
            manifest.append({
                "id": cid, "path": rel(path), "ref": " ".join(run["texts"]), "slice": "long",
                "source": f"openslr/librispeech_asr:clean:test:concat:{'+'.join(run['ids'])}",
                "duration_s": round(dur, 3),
            })
            chapter_runs[chap] = None  # start a fresh run on later utterances
            if len(manifest) >= COUNTS["long"]:
                break
    log(f"long: {len(manifest)} clips")
    return manifest, [m["source"].split("concat:")[1].split("+") for m in manifest]


# ---- technical (gTTS) -----------------------------------------------------
TECH_SENTENCES = [
    "Restart the nginx service with systemctl restart nginx and inspect the logs with journalctl.",
    "After editing the flake, run nixos-rebuild switch to apply the NixOS configuration.",
    "The reverse proxy forwards traffic to the load balancer on port eight thousand eighty.",
    "Configure the firewall to allow SSH on port twenty two and block everything else.",
    "Our CI pipeline builds the Docker image, pushes it to the registry, and deploys to Kubernetes.",
    "Ansible handles configuration drift while Terraform provisions the cloud infrastructure.",
    "Open a merge request in GitLab and wait for the pipeline to pass before merging.",
    "The cron job runs a backup script every night at two in the morning.",
    "Check DNS resolution and the DHCP lease before troubleshooting the VPN tunnel.",
    "The old TLS certificates are still trusted inside this subnet.",
    "We segmented the network into a VLAN for the servers and another for the LDAP directory.",
    "Active Directory group policy pushes the new firewall rules to every workstation.",
    "Veeam backs up the MySQL database and the PostgreSQL cluster to object storage.",
    "Redis caches the session tokens, Postfix relays the mail, and Dovecot serves IMAP.",
    "SpamAssassin scores incoming messages, then the spam folder is cleaned nightly.",
    "Run WSL on the Windows laptop, but the build target is still x86_64.",
    "The main mysql switchover playbook handles failover for the primary database.",
    "Update the configuration file at slash etc slash nixos slash configuration dot nix.",
    "Copy the SSH config to tilde slash dot ssh slash config on the jump host.",
    "Upgrade the kernel to version 6.5.12 and pin the firmware to 1.18.4.",
    "The host address is 192.168.1.10 and the management network is 10.0.0.0/24.",
    "Open port 443 for HTTPS, port 8080 for the admin console, and port 3306 for MySQL.",
    "The status register returns 0xDEADBEEF when the watchdog fires.",
    "The Altium project defines the PCB stackup and the pick and place file.",
    "The PLC drives the stepper motor and the encoder feeds back to the servo controller.",
    "Connect the UART, SPI, and I2C buses to the GPIO header on the dev board.",
    "Modbus over TCP is easier to debug with an oscilloscope on the differential pair.",
    "The Pololu driver controls the chassis motors while the firmware reads the encoders.",
    "The transformer model uses attention over the tokenizer output and the embedding layer is quantized.",
    "Fine tuning reduced the inference latency and improved throughput on the GPU.",
    "Camel case names like carrierCode and exitHook should be typed exactly as written.",
    "Use snake case for variables such as server_setup, work_notes, and merge_requests.",
    "Rebase onto origin main before you push the branch.",
    "The migration script uses nginx, postgresql, and redis in the staging environment.",
]


def synth_technical(sentence: str, out_mp3: str | None = None) -> np.ndarray:
    from gtts import gTTS
    import librosa

    buf = io.BytesIO()
    tts = gTTS(sentence, lang="en", slow=False)
    tts.write_to_fp(buf)
    buf.seek(0)
    y, _ = librosa.load(buf, sr=TARGET_SR, mono=True)
    return np.ascontiguousarray(y, dtype=np.float32)


def build_technical(pinned):
    log("=== slice: technical (gTTS) ===")
    dir_ = os.path.join(DATA_DIR, "technical")
    # technical sentences are deterministic; pinned ids are the sentence indices
    target = pinned.get("technical", list(range(len(TECH_SENTENCES))))
    manifest = []
    for i in target:
        if i >= len(TECH_SENTENCES):
            continue
        cid = f"technical-{i:04d}"
        path = os.path.join(dir_, f"{cid}.wav")
        if os.path.exists(path):
            import soundfile as sf
            a, _ = sf.read(path, dtype="float32")
            dur = len(a) / TARGET_SR
        else:
            ok = False
            for attempt in range(4):
                try:
                    a = synth_technical(TECH_SENTENCES[i])
                    dur = write_wav(path, a)
                    ok = True
                    break
                except Exception as e:
                    log(f"  gTTS retry {attempt + 1} for sentence {i}: {e}")
                    time.sleep(2 + 2 * attempt)
            if not ok:
                log(f"  skipping sentence {i} (gTTS failed)")
                continue
        manifest.append({
            "id": cid, "path": rel(path), "ref": TECH_SENTENCES[i], "slice": "technical",
            "source": "gtts:en:synthetic-technical", "duration_s": round(dur, 3),
        })
        if len(manifest) >= COUNTS["technical"]:
            break
    log(f"technical: {len(manifest)} clips")
    return manifest, target[: len(manifest)]


def build_silence():
    log("=== slice: silence ===")
    dir_ = os.path.join(DATA_DIR, "silence")
    rng = np.random.default_rng(RNG_SEED)
    specs = [
        (4.0, "zero"), (5.5, "zero"), (3.0, "white_low"), (6.2, "pink_low"),
        (6.5, "white_low"), (7.0, "pink_mid"), (8.0, "zero"), (4.5, "pink_low"),
        (9.0, "white_low"), (5.0, "pink_mid"), (3.5, "zero"), (10.0, "white_low"),
    ]
    manifest = []
    for i, (dur, kind) in enumerate(specs):
        n = int(dur * TARGET_SR)
        if kind == "zero":
            a = np.zeros(n, dtype=np.float32)
        elif kind == "white_low":
            a = (rng.standard_normal(n).astype(np.float32)) * 1e-4
        elif kind == "pink_low":
            a = _pink(n, rng) * 2e-4
        else:  # pink_mid: near the app's speech-activity gate (rms ~ 4e-3)
            a = _pink(n, rng) * 1.2e-3
        a = a.astype(np.float32)
        cid = f"silence-{i:04d}"
        path = os.path.join(dir_, f"{cid}.wav")
        write_wav(path, a)
        manifest.append({
            "id": cid, "path": rel(path), "ref": "", "slice": "silence",
            "source": f"synthetic:{kind}:{RNG_SEED}", "duration_s": round(dur, 3),
        })
    log(f"silence: {len(manifest)} clips")
    return manifest


def build_noisy(pinned, clean_cache, accented_cache, tech_cache):
    log("=== slice: noisy ===")
    dir_ = os.path.join(DATA_DIR, "noisy")
    rng = np.random.default_rng(RNG_SEED + 7)
    snrs = [5.0, 10.0, 20.0]
    kinds = ["white", "pink", "babble"]
    # pinned: list of {"base_slice","base_idx","snr","kind","ref"}
    plan = pinned.get("noisy")
    if not plan:
        plan = []
        pool = [("clean", i) for i in range(len(clean_cache))]
        pool += [("accented", i) for i in range(len(accented_cache))]
        pool += [("technical", i) for i in range(len(tech_cache))]
        for k in range(COUNTS["noisy"]):
            bs, bi = pool[k % len(pool)]
            plan.append({
                "base_slice": bs, "base_idx": bi,
                "snr": snrs[k % len(snrs)], "kind": kinds[k % len(kinds)],
            })
    manifest = []
    caches = {"clean": clean_cache, "accented": accented_cache, "technical": tech_cache}
    for k, entry in enumerate(plan):
        base = caches[entry["base_slice"]][entry["base_idx"]]
        ref = base["ref"]
        mixed = mix_at_snr(base["audio"], entry["snr"], rng, entry["kind"])
        cid = f"noisy-{k:04d}"
        path = os.path.join(dir_, f"{cid}.wav")
        dur = write_wav(path, mixed)
        manifest.append({
            "id": cid, "path": rel(path), "ref": ref, "slice": "noisy",
            "source": f"derived:{entry['base_slice']}:{entry['base_idx']}:snr={entry['snr']}:{entry['kind']}",
            "duration_s": round(dur, 3),
        })
    log(f"noisy: {len(manifest)} clips")
    return manifest, plan


def build_technical_noisy(pinned, tech_cache):
    log("=== slice: technical_noisy ===")
    dir_ = os.path.join(DATA_DIR, "technical_noisy")
    rng = np.random.default_rng(RNG_SEED + 11)
    snrs = [10.0, 20.0]
    kinds = ["babble", "pink", "white"]
    plan = pinned.get("technical_noisy")
    if not plan:
        plan = []
        for k in range(COUNTS["technical_noisy"]):
            plan.append({
                "base_idx": k % max(1, len(tech_cache)),
                "snr": snrs[k % len(snrs)], "kind": kinds[k % len(kinds)],
            })
    manifest = []
    for k, entry in enumerate(plan):
        base = tech_cache[entry["base_idx"]]
        mixed = mix_at_snr(base["audio"], entry["snr"], rng, entry["kind"])
        cid = f"technical_noisy-{k:04d}"
        path = os.path.join(dir_, f"{cid}.wav")
        dur = write_wav(path, mixed)
        manifest.append({
            "id": cid, "path": rel(path), "ref": base["ref"], "slice": "technical_noisy",
            "source": f"derived:technical:{entry['base_idx']}:snr={entry['snr']}:{entry['kind']}",
            "duration_s": round(dur, 3),
        })
    log(f"technical_noisy: {len(manifest)} clips")
    return manifest, plan


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-network", action="store_true",
                    help="only (re)build synthetic/silence/noisy from already-downloaded sources")
    ap.add_argument("--reset-pins", action="store_true", help="ignore pinned_ids.json and re-select")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    # Ensure generated audio is never committed, even after a full data/ wipe.
    gi = os.path.join(DATA_DIR, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write("# Audio + generated data are never committed. Keep this file itself.\n*\n!.gitignore\n")
    pinned = {} if args.reset_pins else load_pinned()

    manifest = []

    clean_manifest, clean_ids = [], []
    accented_manifest, accented_ids = [], []
    if not args.no_network:
        clean_manifest, clean_ids = build_clean(pinned)
        accented_manifest, accented_ids = build_accented(pinned)
    manifest += clean_manifest + accented_manifest

    # In-memory caches for noise mixing
    import soundfile as sf
    clean_cache = [{"ref": m["ref"], "audio": sf.read(os.path.join(EVAL_DIR, m["path"]), dtype="float32")[0]}
                   for m in clean_manifest]
    accented_cache = [{"ref": m["ref"], "audio": sf.read(os.path.join(EVAL_DIR, m["path"]), dtype="float32")[0]}
                      for m in accented_manifest]

    # long (network)
    long_manifest, long_groups = [], []
    if not args.no_network:
        long_manifest, long_groups = build_long(pinned)
    manifest += long_manifest

    # technical (network via gTTS, but cached on disk)
    tech_manifest, tech_ids = build_technical(pinned)
    manifest += tech_manifest
    tech_cache = [{"ref": m["ref"], "audio": sf.read(os.path.join(EVAL_DIR, m["path"]), dtype="float32")[0]}
                  for m in tech_manifest]

    # silence (synthetic)
    manifest += build_silence()

    # noisy slices
    noisy_manifest, noisy_plan = build_noisy(pinned, clean_cache, accented_cache, tech_cache)
    manifest += noisy_manifest
    tech_noisy_manifest, tnoisy_plan = build_technical_noisy(pinned, tech_cache)
    manifest += tech_noisy_manifest

    # Persist manifest
    with open(MANIFEST_PATH, "w") as f:
        for m in manifest:
            f.write(json.dumps(m) + "\n")
    log(f"wrote {MANIFEST_PATH} ({len(manifest)} clips)")

    # Persist pinned selection
    new_pins = dict(pinned)
    new_pins.update({
        "clean": clean_ids,
        "accented": accented_ids,
        "long": long_groups,
        "technical": tech_ids,
        "noisy": noisy_plan,
        "technical_noisy": tnoisy_plan,
    })
    save_pinned(new_pins)

    # Summary
    from collections import Counter
    counts = Counter(m["slice"] for m in manifest)
    log("slice counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    log(f"TOTAL clips: {len(manifest)}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
    # datasets streaming can leave background parquet threads alive; force exit.
    os._exit(0)

#!/usr/bin/env python3
"""
Prepare the Cohere Transcribe weights for distribution as GitHub Release assets.

Why: CohereLabs/cohere-transcribe-03-2026 is Apache-2.0 licensed, so the weights
may be mirrored and redistributed (with license + attribution). The app's
first-run installer downloads these assets instead of requiring a Hugging Face
account. GitHub caps individual release files at 2 GiB, so the unpacked model
(4.13 GB bf16) is packed into ONE tar, split into raw byte-range parts (tar is
plainly concatenable across any byte boundary), and each part is xz-compressed
independently. The installer downloads the parts, decompresses, concatenates,
and extracts them back into a flat `models/cohere` directory.

Output (in --outdir):
  cohere-transcribe-<revision>.part<N>.xz   split, compressed weight parts
  cohere-transcribe-<revision>.SHA256SUMS   "<sha256>  <filename>" per part

Usage:
  python3 scripts/prepare_model_release.py                # auto-find HF cache
  python3 scripts/prepare_model_release.py --model-dir <dir> --out dist/model

Requires the `xz` binary (present on Ubuntu runners and NixOS); falls back to
Python's lzma module (single-threaded) if xz is unavailable.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

REPO_ID = "CohereLabs/cohere-transcribe-03-2026"
REVISION = "499888924f5f1313b48ab0686c8f3a94178a4709"

# model.safetensors sha256 == its HF blob id (content-addressed cache)
SAFETENSORS_SHA256 = "987bd3e141c7bfdb5a78f5db11397ee7737308357e6cc0a3f36a4979b158137a"

# Files from the HF snapshot that transformers needs for a local offline load.
REQUIRED_FILES = [
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "modeling_cohere_asr.py",
    "configuration_cohere_asr.py",
    "processing_cohere_asr.py",
    "tokenization_cohere_asr.py",
    "processor_config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "tokenizer.model",
    "special_tokens_map.json",
]

NOTICE = (
    "Cohere Transcribe 2B (cohere-transcribe-03-2026)\n"
    "Automatic Speech Recognition model by Cohere / Cohere Labs.\n"
    f"Revision: {REVISION}\n"
    f"Source: https://huggingface.co/{REPO_ID}\n"
    "\n"
    "This model is licensed under the Apache License, Version 2.0 "
    "(see the LICENSE file in this directory).\n"
    "Weights mirrored for distribution via the voice-transcriber GitHub release."
)


def find_hf_cache_model_dir():
    """Locate the downloaded snapshot inside ~/.cache/huggingface/hub."""
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    repo_dir = os.path.join(hub, "models--" + REPO_ID.replace("/", "--"))
    snap = os.path.join(repo_dir, "snapshots", REVISION)
    if os.path.isdir(snap):
        return snap
    refs_main = os.path.join(repo_dir, "refs", "main")
    if os.path.exists(refs_main):
        snap2 = os.path.join(repo_dir, "snapshots", open(refs_main).read().strip())
        if os.path.isdir(snap2) and all(os.path.exists(os.path.join(snap2, f)) for f in REQUIRED_FILES):
            return snap2
    # fall back to any snapshot that looks complete
    import glob
    for cand in sorted(glob.glob(os.path.join(repo_dir, "snapshots", "*"))):
        if all(os.path.exists(os.path.join(cand, f)) for f in REQUIRED_FILES):
            return cand
    return None


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def find_xz():
    xz = shutil.which("xz")
    if xz:
        return xz
    return None


def xz_compress(src, dst, level=6):
    """Compress src -> dst using system xz (multi-threaded) or lzma fallback."""
    xz = find_xz()
    if xz:
        subprocess.run([xz, f"-{level}e", "-T0", "-c", src],
                       stdout=open(dst, "wb"), check=True)
    else:
        import lzma
        filters = [{"id": lzma.FILTER_LZMA2, "preset": level | lzma.PRESET_EXTREME}]
        with open(src, "rb") as fin, lzma.open(dst, "wb", filters=filters) as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
    return os.path.getsize(dst)


def build_tar(model_dir, staging, out_tar):
    """Copy the flat model files + license into staging, then tar them."""
    os.makedirs(staging, exist_ok=True)
    missing = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(model_dir, f))]
    if missing:
        print(f"ERROR: source model dir is missing files: {missing}")
        sys.exit(2)

    for f in REQUIRED_FILES:
        shutil.copy2(os.path.join(model_dir, f), os.path.join(staging, f))

    # Apache-2.0 redistribution compliance: ship the license + attribution.
    here = os.path.dirname(os.path.abspath(__file__))
    lic = os.path.join(here, "..", "config", "licenses", "Cohere-Apache-2.0.txt")
    if os.path.exists(lic):
        shutil.copy2(lic, os.path.join(staging, "LICENSE"))
    else:
        print("WARNING: config/licenses/Cohere-Apache-2.0.txt not found; "
              "model LICENSE file will be missing from the release asset.")
    with open(os.path.join(staging, "NOTICE"), "w") as f:
        f.write(NOTICE)

    with tarfile.open(out_tar, "w", format=tarfile.GNU_FORMAT) as tf:
        for name in sorted(os.listdir(staging)):
            tf.add(os.path.join(staging, name), arcname=name)


def split_file(path, parts_dir, n_parts):
    """Split `path` into n_parts raw byte-range pieces (concatenatable)."""
    total = os.path.getsize(path)
    part_sizes = []
    base = total // n_parts
    rem = total % n_parts
    # even split (first `rem` parts get one extra byte)
    for i in range(n_parts):
        part_sizes.append(base + (1 if i < rem else 0))
    paths = []
    with open(path, "rb") as fin:
        for i, size in enumerate(part_sizes, start=1):
            p = os.path.join(parts_dir, f"piece{i}")
            with open(p, "wb") as fout:
                remaining = size
                while remaining > 0:
                    buf = fin.read(min(1 << 20, remaining))
                    if not buf:
                        break
                    fout.write(buf)
                    remaining -= len(buf)
            paths.append(p)
    return paths


def verify(outdir, prefix, parts_raw, original_tar, model_safetensors_sha):
    """Re-assemble decompressed parts and confirm the tar round-trips."""
    print("\nVerifying parts ...")
    xz = find_xz()
    combined = os.path.join(outdir, ".verify.tar")
    with open(combined, "wb") as out:
        for i, piece in enumerate(parts_raw, start=1):
            part_xz = os.path.join(outdir, f"{prefix}.part{i}.xz")
            if xz:
                with subprocess.Popen([xz, "-dc", part_xz], stdout=subprocess.PIPE) as proc:
                    assert proc.stdout is not None
                    shutil.copyfileobj(proc.stdout, out, 1 << 20)
                    rc = proc.wait()
                if rc != 0:
                    raise RuntimeError(f"xz -dc failed for {part_xz}")
            else:
                import lzma
                with lzma.open(part_xz, "rb") as fin:
                    shutil.copyfileobj(fin, out, 1 << 20)
    ok_sizes = os.path.getsize(combined) == os.path.getsize(original_tar)
    print(f"  reassembled tar size match: {ok_sizes} "
          f"({os.path.getsize(combined)} vs {os.path.getsize(original_tar)})")

    # Stream-hash model.safetensors inside the reassembled tar.
    member_sha = None
    with tarfile.open(combined, "r") as tf:
        m = tf.extractfile("model.safetensors")
        assert m is not None, "model.safetensors missing from tar"
        h = hashlib.sha256()
        while True:
            b = m.read(1 << 20)
            if not b:
                break
            h.update(b)
        member_sha = h.hexdigest()
    ok_hash = member_sha == model_safetensors_sha
    print(f"  model.safetensors sha256 match: {ok_hash}")
    os.remove(combined)
    return ok_sizes and ok_hash


def main():
    ap = argparse.ArgumentParser(description="Package Cohere Transcribe weights "
                                             "as split-xz GitHub Release assets.")
    ap.add_argument("--model-dir", default=None,
                    help="Flat directory with the model files (default: locate "
                         "the HF cache snapshot automatically)")
    ap.add_argument("--out", default=os.path.join("dist", "model"),
                    help="Output directory for the release assets")
    ap.add_argument("--parts", type=int, default=2,
                    help="Number of raw tar parts (default 2; each stays well "
                         "under GitHub's 2 GiB per-file limit once xz'd)")
    ap.add_argument("--xz-level", type=int, default=6,
                    help="xz preset level (default 6, ~70.4%% ratio; 9 gains "
                         "almost nothing on weights)")
    ap.add_argument("--skip-verify", action="store_true",
                    help="Skip the decompress-and-hash round-trip check")
    args = ap.parse_args()

    model_dir = args.model_dir or find_hf_cache_model_dir()
    if not model_dir or not os.path.isdir(model_dir):
        print("ERROR: could not find the model. Pass --model-dir explicitly.")
        sys.exit(2)

    print(f"Source model dir: {model_dir}")
    prefix = f"cohere-transcribe-{REVISION}"
    os.makedirs(args.out, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vt-model-release-") as tmp:
        staging = os.path.join(tmp, "staging")
        tar_path = os.path.join(tmp, "model.tar")

        print(f"Staging {len(REQUIRED_FILES)} files + LICENSE/NOTICE ...")
        build_tar(model_dir, staging, tar_path)
        raw = os.path.getsize(tar_path)
        print(f"tar created: {raw/1e9:.2f} GB")

        pieces_dir = os.path.join(tmp, "pieces")
        os.makedirs(pieces_dir)
        print(f"Splitting into {args.parts} raw parts ...")
        pieces = split_file(tar_path, pieces_dir, args.parts)
        for i, p in enumerate(pieces, 1):
            print(f"  piece{i}: {os.path.getsize(p)/1e9:.2f} GB")

        print(f"Compressing {args.parts} parts with xz -{args.xz_level}e -T0 ...")
        for i, piece in enumerate(pieces, 1):
            dst = os.path.join(args.out, f"{prefix}.part{i}.xz")
            size = xz_compress(piece, dst, args.xz_level)
            print(f"  {os.path.basename(dst)}: {size/1e9:.2f} GB "
                  f"({100.0*size/os.path.getsize(piece):.1f}% of raw piece)")

        sums_path = os.path.join(args.out, f"{prefix}.SHA256SUMS")
        with open(sums_path, "w") as f:
            for i in range(1, args.parts + 1):
                p = os.path.join(args.out, f"{prefix}.part{i}.xz")
                f.write(f"{sha256_file(p)}  {os.path.basename(p)}\n")
        print(f"SHA256SUMS -> {sums_path}")

        if not args.skip_verify:
            ok = verify(args.out, prefix, pieces, tar_path, SAFETENSORS_SHA256)
            if not ok:
                print("VERIFICATION FAILED — do not publish these assets.")
                sys.exit(1)
            print("Verification passed: parts decompress, concatenate, and the "
                  "weights hash identically to the source model.\n")

    print("Assets ready for upload:")
    total = 0
    for i in range(1, args.parts + 1):
        p = os.path.join(args.out, f"{prefix}.part{i}.xz")
        total += os.path.getsize(p)
        print(f"  {p}  ({os.path.getsize(p)/1e9:.2f} GB)")
    print(f"  {sums_path}")
    print(f"Total: {total/1e9:.2f} GB  (source: {raw/1e9:.2f} GB unpacked)")


if __name__ == "__main__":
    main()

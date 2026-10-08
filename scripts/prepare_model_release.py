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

# Single source of truth for what this package contains: importing rather than
# re-declaring means the builder and the installer cannot disagree about the
# revision or the file list, which would otherwise drift silently on a model
# bump. ``--model`` selects the registry entry to package.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))
from model_download import get_spec  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_notice(spec):
    """The NOTICE shipped beside the license inside the release archive."""
    return (
        f"{spec.display_name} ({spec.repo_id.rsplit('/', 1)[-1]})\n"
        f"{spec.description}\n"
        f"Revision: {spec.revision}\n"
        f"Source: https://huggingface.co/{spec.repo_id}\n"
        "\n"
        f"This model is licensed under {spec.license_name} "
        "(see the LICENSE file in this directory).\n"
        "Weights mirrored for distribution via the voice-transcriber GitHub release."
    )


def find_hf_cache_model_dir(spec):
    """Locate the downloaded snapshot inside ~/.cache/huggingface/hub."""
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    repo_dir = os.path.join(hub, "models--" + spec.repo_id.replace("/", "--"))
    snap = os.path.join(repo_dir, "snapshots", spec.revision)
    if os.path.isdir(snap):
        return snap
    refs_main = os.path.join(repo_dir, "refs", "main")
    if os.path.exists(refs_main):
        snap2 = os.path.join(repo_dir, "snapshots", open(refs_main).read().strip())
        if os.path.isdir(snap2) and all(os.path.exists(os.path.join(snap2, f)) for f in spec.package_files):
            return snap2
    # fall back to any snapshot that looks complete
    import glob
    for cand in sorted(glob.glob(os.path.join(repo_dir, "snapshots", "*"))):
        if all(os.path.exists(os.path.join(cand, f)) for f in spec.package_files):
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


def build_tar(spec, model_dir, staging, out_tar):
    """Copy the flat model files + license into staging, then tar them."""
    os.makedirs(staging, exist_ok=True)
    missing = [f for f in spec.package_files if not os.path.exists(os.path.join(model_dir, f))]
    if missing:
        print(f"ERROR: source model dir is missing files: {missing}")
        sys.exit(2)

    for f in spec.package_files:
        shutil.copy2(os.path.join(model_dir, f), os.path.join(staging, f))

    # Redistribution compliance: ship the license + attribution declared by the
    # spec, so a second model needs no edit here.
    lic = spec.license_file
    if lic and not os.path.isabs(lic):
        lic = os.path.join(REPO_ROOT, lic)
    if lic and os.path.exists(lic):
        shutil.copy2(lic, os.path.join(staging, "LICENSE"))
    else:
        print(f"WARNING: {spec.license_file} not found; "
              "model LICENSE file will be missing from the release asset.")
    with open(os.path.join(staging, "NOTICE"), "w") as f:
        f.write(build_notice(spec))

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


def verify(spec, outdir, parts_raw, original_tar):
    """Re-assemble decompressed parts and confirm the tar round-trips."""
    print("\nVerifying parts ...")
    prefix = spec.asset_prefix
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

    # Stream-hash every redistributed file the spec pins, so the check is
    # format-agnostic (safetensors, GGUF, ONNX, ...).
    ok_hash = True
    with tarfile.open(combined, "r") as tf:
        for fname, expected in spec.digests.items():
            m = tf.extractfile(fname)
            assert m is not None, f"{fname} missing from tar"
            h = hashlib.sha256()
            while True:
                b = m.read(1 << 20)
                if not b:
                    break
                h.update(b)
            match = h.hexdigest() == expected
            ok_hash = ok_hash and match
            print(f"  {fname} sha256 match: {match}")
    os.remove(combined)
    return ok_sizes and ok_hash


def main():
    ap = argparse.ArgumentParser(description="Package a registered model's weights "
                                             "as split-xz GitHub Release assets.")
    ap.add_argument("--model", default="cohere",
                    help="Registry model to package (default: cohere); the "
                         "registry lives in src/voice_transcriber/model_download.py")
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
    ap.add_argument("--zip", dest="zip_path", default=None,
                    help="Also write a single transportable .zip of the parts + "
                         "SHA256SUMS (USB / airgapped delivery)")
    args = ap.parse_args()

    spec = get_spec(args.model)
    model_dir = args.model_dir or find_hf_cache_model_dir(spec)
    if not model_dir or not os.path.isdir(model_dir):
        print("ERROR: could not find the model. Pass --model-dir explicitly.")
        sys.exit(2)

    print(f"Model: {spec.name} ({spec.repo_id}@{spec.revision[:12]})")
    print(f"Source model dir: {model_dir}")
    prefix = spec.asset_prefix
    os.makedirs(args.out, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vt-model-release-") as tmp:
        staging = os.path.join(tmp, "staging")
        tar_path = os.path.join(tmp, "model.tar")

        print(f"Staging {len(spec.package_files)} files + LICENSE/NOTICE ...")
        build_tar(spec, model_dir, staging, tar_path)
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
            ok = verify(spec, args.out, pieces, tar_path)
            if not ok:
                print("VERIFICATION FAILED — do not publish these assets.")
                sys.exit(1)
            print("Verification passed: parts decompress, concatenate, and the "
                  "weights hash identically to the source model.\n")

    if args.zip_path:
        import zipfile

        print(f"Writing transportable bundle -> {args.zip_path}")
        with zipfile.ZipFile(args.zip_path, "w",
                             compression=zipfile.ZIP_DEFLATED,
                             allowZip64=True) as zf:
            for i in range(1, args.parts + 1):
                p = os.path.join(args.out, f"{prefix}.part{i}.xz")
                zf.write(p, arcname=os.path.basename(p))
            zf.write(sums_path, arcname=os.path.basename(sums_path))
        print(f"  {args.zip_path}: {os.path.getsize(args.zip_path)/1e9:.2f} GB\n")

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

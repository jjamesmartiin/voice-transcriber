#!/usr/bin/env python3
"""Build a human-reviewable report from a recorded eval result JSON + manifest.

The point of this artifact is to let a human validate **what the audio was**
against **what the pipeline transcribed**: every clip gets an inline audio
player, the reference, the hypothesis, and a word-level diff, with per-clip WER
and a per-slice roll-up.

It reads an already-recorded ``--json`` output from ``eval/score.py`` plus the
manifest, and does **not** touch the model. Regenerate the report with:

    nix develop --command python eval/review_report.py

Inputs:  eval/results.json, eval/manifest.jsonl
Outputs: eval/report.html (self-contained, no external CSS/JS, audio referenced
         by relative path -- no base64), eval/review.json (machine readable)

Override any path with --results/--manifest/--out/--json. Works with any
score.py result JSON, including older files whose ``meta`` predates the
self-describing fields (the report simply renders what is present).

Word diff: ref and hyp are tokenised on whitespace. Tokens are compared on a
key of lowercased ``[a-z0-9]`` only, so case- and punctuation-only differences
(which scoring normalisation also ignores) are not flagged. Changed ref tokens
are marked red, changed hyp tokens green.
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import sys

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(EVAL_DIR)


def _key(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def word_diff(ref: str, hyp: str):
    """Return (ref_tokens, ref_ops, hyp_tokens, hyp_ops) with op in equal/del/ins."""
    rt = ref.split()
    ht = hyp.split()
    rk = [_key(t) for t in rt]
    hk = [_key(t) for t in ht]
    sm = difflib.SequenceMatcher(a=rk, b=hk, autojunk=False)
    ref_ops = ["equal"] * len(rt)
    hyp_ops = ["equal"] * len(ht)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            ref_ops[i] = "del"
        for j in range(j1, j2):
            hyp_ops[j] = "ins"
    return rt, ref_ops, ht, hyp_ops


def _render_tokens(tokens, ops) -> str:
    if not tokens:
        return '<span class="empty">(empty)</span>'
    out = []
    for tok, op in zip(tokens, ops, strict=True):
        esc = html.escape(tok)
        if op == "equal":
            out.append(esc)
        else:
            out.append(f'<span class="{op}">{esc}</span>')
    return " ".join(out)


def _audio_rel(manifest_path: str, out_html: str) -> str:
    """Path to the clip audio, relative to the directory holding report.html."""
    abs_audio = os.path.join(EVAL_DIR, manifest_path)
    return os.path.relpath(abs_audio, os.path.dirname(os.path.abspath(out_html)))


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _load_manifest(path: str) -> dict:
    entries = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                entries[e["id"]] = e
    return entries


def _clip_wer(r: dict) -> float:
    if "wer" in r:
        return float(r["wer"])
    we, wr = r.get("word_edits", 0), r.get("ref_words", 0)
    if wr:
        return we / wr
    return 0.0 if not r.get("hyp", "").strip() else 1.0


def _slice_summary(clips: list) -> dict:
    out = {}
    for r in clips:
        s = r["slice"]
        d = out.setdefault(s, {"n": 0, "we": 0, "wr": 0, "ce": 0, "cr": 0,
                               "exact": 0, "empty": 0})
        d["n"] += 1
        d["we"] += r.get("word_edits", 0)
        d["wr"] += r.get("ref_words", 0)
        d["ce"] += r.get("char_edits", 0)
        d["cr"] += r.get("ref_chars", 0)
        d["exact"] += 1 if r.get("exact") else 0
        d["empty"] += 1 if r.get("empty") or not r.get("hyp", "").strip() else 0
    for s, d in out.items():
        d["wer"] = (d["we"] / d["wr"]) if d["wr"] else None
        d["cer"] = (d["ce"] / d["cr"]) if d["cr"] else None
        d["exact_rate"] = (d["exact"] / d["n"]) if d["n"] else None
        d["silence_halluc_rate"] = (d["empty"] / d["n"]) if s == "silence" and d["n"] else None
    return out


def _fmt_pct(x):
    return "—" if x is None else f"{100.0 * x:.2f}%"


_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Transcriber eval review</title>
<style>
  :root { --del:#c62828; --ins:#2e7d32; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0 auto; max-width: 60rem; padding: 1.25rem; color: #1b1b1b;
         background: #fafafa; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
  h2 { font-size: 1.1rem; margin: 1.5rem 0 .5rem; }
  .meta { color: #555; font-size: .85rem; line-height: 1.5; }
  .meta code { background: #eee; padding: 0 .2rem; border-radius: 3px; }
  table.summary { border-collapse: collapse; margin: .5rem 0 1rem; font-size: .9rem; }
  table.summary th, table.summary td { border: 1px solid #ddd; padding: .25rem .5rem;
         text-align: right; }
  table.summary th:first-child, table.summary td:first-child { text-align: left; }
  table.summary tr.overall td { font-weight: 600; background: #f0f0f0; }
  details.slice { border: 1px solid #e0e0e0; border-radius: 6px; margin: .5rem 0;
         background: #fff; }
  details.slice > summary { cursor: pointer; padding: .5rem .75rem; font-weight: 600;
         background: #f5f5f5; border-radius: 6px; }
  article.clip { border-top: 1px solid #eee; padding: .75rem; }
  article.clip h3 { font-size: .95rem; margin: 0 0 .4rem; font-family: ui-monospace,
         monospace; }
  .badge { font-size: .75rem; font-weight: 500; padding: .05rem .4rem;
         border-radius: 999px; background: #eee; margin-left: .4rem; }
  audio { width: 100%; max-width: 32rem; margin: .15rem 0 .5rem; }
  .line { font-size: .95rem; line-height: 1.7; margin: .15rem 0; }
  .lbl { font-size: .7rem; font-weight: 700; color: #888; letter-spacing: .05em;
         margin-right: .4rem; }
  .del { background: #fdecea; color: var(--del); text-decoration: line-through; }
  .ins { background: #e8f5e9; color: var(--ins); font-weight: 600; }
  .empty { color: #999; font-style: italic; }
  .wer-good { color: #2e7d32; } .wer-mid { color: #ef6c00; } .wer-bad { color: #c62828; }
</style>
</head>
<body>
"""


def build_html(results: dict, manifest: dict, out_html: str, results_path: str,
               manifest_path: str) -> str:
    clips = results.get("results") or results.get("clips") or []
    meta = results.get("meta", {})
    slices = _slice_summary(clips)

    parts = [_HTML_HEAD]
    parts.append("<h1>Voice Transcriber eval review</h1>")
    parts.append('<div class="meta">')
    parts.append(f"results: <code>{html.escape(os.path.relpath(results_path, REPO_ROOT))}</code> &middot; "
                 f"manifest: <code>{html.escape(os.path.relpath(manifest_path, REPO_ROOT))}</code><br>")
    for key in ("backend", "int8_dynamic", "number_digits", "blocks_ms", "skip_slm",
                "git_rev", "git_dirty"):
        if key in meta:
            parts.append(f"{html.escape(key)}=<code>{html.escape(str(meta[key]))}</code> &middot; ")
    cs = meta.get("config_source")
    if isinstance(cs, dict):
        state = "loaded" if cs.get("loaded") else "MISSING -> defaults"
        parts.append(f"config_source=<code>{html.escape(str(cs.get('path', '')))} "
                     f"({state})</code> &middot; ")
    if meta.get("overall_wer") is not None:
        parts.append(f"overall_wer=<code>{_fmt_pct(float(meta['overall_wer']))}</code> &middot; ")
    if meta.get("invocation"):
        parts.append(f"<br>invocation: <code>{html.escape(' '.join(map(str, meta['invocation'])))}</code>")
    parts.append("</div>")

    # ---- per-slice roll-up ----
    parts.append("<h2>Per-slice roll-up</h2>")
    parts.append('<table class="summary"><tr><th>slice</th><th>n</th><th>WER</th>'
                 "<th>CER</th><th>exact</th><th>empties</th></tr>")
    for sl in sorted(slices):
        s = slices[sl]
        parts.append(
            f"<tr><td>{html.escape(sl)}</td><td>{s['n']}</td>"
            f"<td>{_fmt_pct(s['wer'])}</td><td>{_fmt_pct(s['cer'])}</td>"
            f"<td>{_fmt_pct(s['exact_rate'])}</td>"
            f"<td>{s['empty']}{' (halluc)' if sl == 'silence' else ''}</td></tr>"
        )
    speech = [r for r in clips if r["slice"] != "silence"]
    we = sum(r.get("word_edits", 0) for r in speech)
    wr = sum(r.get("ref_words", 0) for r in speech)
    ce = sum(r.get("char_edits", 0) for r in speech)
    cr = sum(r.get("ref_chars", 0) for r in speech)
    ex = sum(1 for r in speech if r.get("exact"))
    parts.append(
        f'<tr class="overall"><td>overall (speech)</td><td>{len(speech)}</td>'
        f"<td>{_fmt_pct(we / wr if wr else None)}</td>"
        f"<td>{_fmt_pct(ce / cr if cr else None)}</td>"
        f"<td>{_fmt_pct(ex / len(speech) if speech else None)}</td><td>—</td></tr>"
    )
    parts.append("</table>")

    # ---- per-clip sections ----
    for sl in sorted(slices):
        sl_clips = [r for r in clips if r["slice"] == sl]
        parts.append(f'<details class="slice" open><summary>{html.escape(sl)} '
                     f'({len(sl_clips)} clips, WER {_fmt_pct(slices[sl]["wer"])})</summary>')
        for r in sl_clips:
            clip_id = r["id"]
            entry = manifest.get(clip_id, {})
            wer = _clip_wer(r)
            cls = "wer-good" if wer == 0 else ("wer-mid" if wer < 0.2 else "wer-bad")
            parts.append(f'<article class="clip" id="clip-{html.escape(clip_id)}">')
            parts.append(
                f'<h3>{html.escape(clip_id)} <span class="badge">{html.escape(sl)}</span> '
                f'<span class="badge">{r.get("duration_s", entry.get("duration_s", "?"))}s</span> '
                f'<span class="badge {cls}">WER {_fmt_pct(wer)}</span> '
                f'<span class="badge">edits {r.get("word_edits", "?")}/{r.get("ref_words", "?")}</span>'
                f"</h3>"
            )
            mp = entry.get("path")
            if mp:
                rel = _audio_rel(mp, out_html)
                parts.append(f'<audio controls preload="none" src="{html.escape(rel)}"></audio>')
            rt, rops, ht, hops = word_diff(r.get("ref", ""), r.get("hyp", ""))
            parts.append(f'<div class="line"><span class="lbl">REF</span>{_render_tokens(rt, rops)}</div>')
            parts.append(f'<div class="line"><span class="lbl">HYP</span>{_render_tokens(ht, hops)}</div>')
            if entry.get("source"):
                parts.append(f'<div class="meta">source: {html.escape(str(entry["source"]))}</div>')
            parts.append("</article>")
        parts.append("</details>")

    parts.append("</body></html>\n")
    return "".join(parts)


def build_json(results: dict, manifest: dict) -> dict:
    clips = results.get("results") or results.get("clips") or []
    out_clips = []
    for r in clips:
        entry = manifest.get(r["id"], {})
        rt, rops, ht, hops = word_diff(r.get("ref", ""), r.get("hyp", ""))
        out_clips.append({
            "id": r["id"],
            "slice": r["slice"],
            "audio": entry.get("path"),
            "duration_s": r.get("duration_s", entry.get("duration_s")),
            "wer": _clip_wer(r),
            "clean_wer": r.get("clean_wer"),
            "exact": bool(r.get("exact")),
            "ref": r.get("ref", ""),
            "hyp": r.get("hyp", ""),
            "diff": {
                "ref": [{"t": t, "op": o} for t, o in zip(rt, rops, strict=True)],
                "hyp": [{"t": t, "op": o} for t, o in zip(ht, hops, strict=True)],
            },
        })
    return {"meta": results.get("meta", {}),
            "slices": _slice_summary(clips),
            "clips": out_clips}


def main():
    ap = argparse.ArgumentParser(
        description="Build eval/report.html + eval/review.json from a recorded "
                    "score.py results JSON and the manifest (no model run).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(EVAL_DIR, "results.json"),
                    help="recorded score.py JSON")
    ap.add_argument("--manifest", default=os.path.join(EVAL_DIR, "manifest.jsonl"))
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "report.html"))
    ap.add_argument("--json", dest="json_out", default=os.path.join(EVAL_DIR, "review.json"))
    args = ap.parse_args()

    if not os.path.exists(args.results):
        print(f"[review] missing results JSON: {args.results}", file=sys.stderr)
        return 2
    if not os.path.exists(args.manifest):
        print(f"[review] missing manifest: {args.manifest}", file=sys.stderr)
        return 2

    results = _load_json(args.results)
    manifest = _load_manifest(args.manifest)
    n = len(results.get("results") or results.get("clips") or [])
    missing = [c["id"] for c in (results.get("results") or results.get("clips") or [])
               if c["id"] not in manifest]
    if missing:
        print(f"[review] warning: {len(missing)} clips missing from manifest "
              f"(no audio path): {', '.join(missing[:5])}", file=sys.stderr)

    html_doc = build_html(results, manifest, args.out, args.results, args.manifest)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(html_doc)
    review = build_json(results, manifest)
    with open(args.json_out, "w") as f:
        json.dump(review, f, indent=2)
    print(f"[review] wrote {args.out} ({len(html_doc) / 1024:.0f} KiB) and {args.json_out} "
          f"for {n} clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())

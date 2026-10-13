#!/usr/bin/env python3
"""Local SLM / vLLM client for the optional dictation polish pass.

Deliberately a leaf module. It is the only place in ``src/`` that speaks HTTP to
the SLM, so importing the text cleaner (``post_processor``) no longer drags a
network client into an otherwise offline-first package. ``post_processor``
reaches it lazily, from inside ``clean_speech_transcription``.

Split out of ``post_processor.py`` (audit §3). Behaviour is unchanged; this is a
move. The offline-first property is pinned by ``tests/shared/test_offline_import.py``.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# Keep both import spellings pointed at one module object. ``main.py`` and the
# test suite import the bare name, package callers use ``voice_transcriber.``;
# a split object would make a monkeypatch miss the module the code actually
# calls. Mirrors the guard at the top of ``post_processor.py``.
_current_mod = sys.modules.get(__name__)
if _current_mod is not None:
    sys.modules.setdefault("slm_client", _current_mod)
    sys.modules.setdefault("voice_transcriber.slm_client", _current_mod)


def _sanitize_slm_output(input_text: str, output_text: str) -> str:
    """
    Strip spurious quotes, markdown, XML tags, and punctuation artifacts from SLM output.
    Apostrophes inside words ("it's") are preserved; quote marks are only removed when
    the input itself contained none (dictation rarely includes literal quotes).
    """
    clean_output = output_text.strip()
    # Extract content inside <cleaned_text> tags if present, or strip any stray XML tags
    xml_match = re.search(r'<cleaned_text>(.*?)</cleaned_text>', clean_output, re.DOTALL | re.IGNORECASE)
    if xml_match:
        clean_output = xml_match.group(1).strip()
    else:
        if '<' in clean_output:
            clean_output = re.sub(r'</?[a-zA-Z0-9_-]+>', '', clean_output).strip()

    # Remove enclosing quotes if model wrapped output in quotes
    if clean_output.startswith('"') and clean_output.endswith('"') and len(clean_output) > 2:
        clean_output = clean_output[1:-1].strip()

    # Double quotes / curly quotes are never spoken in dictation: remove them when the
    # input itself had none. Single-quote MARKS at word boundaries (e.g. 'word') are
    # always removed; apostrophes inside words (contractions like "it's") are preserved
    # by the word-boundary rules regardless of the input.
    if '"' not in input_text and '“' not in input_text and '”' not in input_text:
        clean_output = re.sub(r'["“”]', '', clean_output)
    if "'" in clean_output:
        clean_output = re.sub(r"(?<!\w)'(?=\w)|(?<=\w)'(?!\w)|(?<=[.,;:!?])'(?=\s|$)", '', clean_output)
    if '‘' in clean_output or '’' in clean_output:
        clean_output = re.sub(r"[‘’]", '', clean_output)
    # Remove markdown artifacts (code fences, backticks, bold/italic markers)
    if '`' in clean_output or '*' in clean_output or '_' in clean_output or '#' in clean_output:
        clean_output = re.sub(r'`{1,3}|\*\*|\*|__|#{1,6}\s?', '', clean_output)

    # Normalize spacing after punctuation and stray punctuation sequences
    clean_output = re.sub(r'([,.!?;:])([a-zA-Z0-9])', r'\1 \2', clean_output)
    clean_output = re.sub(r'([.!?])\s*[.,;:]+', r'\1', clean_output)
    clean_output = re.sub(r'\s+([,.;:])', r'\1', clean_output)
    clean_output = re.sub(r'([,.;:])\s*([,.;:])', r'\1', clean_output)
    return clean_output.strip()


VLLM_API_URL = os.environ.get("VT_VLLM_URL", "http://localhost:8000/v1/chat/completions")

# A transcript must reach the local SLM and nothing else. `urlopen` honours
# `http_proxy` / `HTTPS_PROXY` / macOS `_scproxy`, and none of those can tell
# "localhost:8000" from the open internet, so a machine with a proxy configured
# would ship dictation to it. An empty ProxyHandler opts out of all of them; this
# is what makes README's "talks only to 127.0.0.1" true. Pinned by
# tests/shared/test_slm_loopback.py.
_VLLM_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

#: A transcript must reach a *local* SLM unless the user deliberately opts into
#: a remote one. The formatter has the same gate (`VT_FORMATTER_ALLOW_REMOTE=1`);
#: this mirrors it so `VT_VLLM_URL` cannot silently post dictation to another
#: machine. See docs/TODO.md.
ENV_ALLOW_REMOTE = "VT_VLLM_ALLOW_REMOTE"


class SlmRemoteRefused(ValueError):
    """``VT_VLLM_URL`` names another machine and the opt-in flag is not set."""


def _is_loopback(host: str) -> bool:
    """Whether ``host`` names this machine's own loopback.

    Decided without a resolver, so a hostile DNS answer cannot steer it: a name
    other than ``localhost`` is not accepted even if it happens to resolve to
    ``127.0.0.1``. Deliberately a local copy of the formatter's check - a 10-line
    duplicate is cheaper than coupling this legacy leaf module to the formatter
    package.
    """
    if host.lower() == "localhost":
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _slm_endpoint(url: str) -> tuple[str, int]:
    """``(host, port)`` for the SLM URL, or raise if we must not talk to it.

    Refusals, all on the grounds that the failure would otherwise be silent:

    * anything that is not an ``http://`` or ``https://`` URL - a scheme-less
      ``127.0.0.1:8000`` would be parsed as a path and POSTed nowhere useful;
    * any host that is not this machine, unless ``VT_VLLM_ALLOW_REMOTE=1`` says
      otherwise. A remote ``http://`` endpoint carries the transcript in the
      clear; ``https`` is allowed (``urllib`` speaks TLS) but the honest advice
      is still to tunnel it.

    Mirrors :func:`formatters.llama_server._endpoint` without importing it. The
    formatter refuses ``https`` only because its hand-rolled transport has no TLS;
    here ``urllib`` does, so an encrypted remote endpoint is not refused.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:  # bad port, malformed IPv6 literal, ...
        raise SlmRemoteRefused(f"{url!r} is not a usable SLM URL: {exc}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SlmRemoteRefused(f"{url!r} is not an http:// or https:// URL")
    if not host:
        raise SlmRemoteRefused(f"{url!r} names no host")
    if not _is_loopback(host) and (os.environ.get(ENV_ALLOW_REMOTE) or "").strip() != "1":
        raise SlmRemoteRefused(
            f"VT_VLLM_URL points at {host}, which is not this machine; the SLM "
            f"polish pass would send your transcript there in the clear. Set "
            f"{ENV_ALLOW_REMOTE}=1 to allow that (tunnel it if the network is "
            f"not trusted), or point VT_VLLM_URL back at 127.0.0.1."
        )
    return host, 80 if port is None else port


# SLM / vLLM Model Auto-Download & Manual Download Links:
# ------------------------------------------------------------------------------
# 1. Automatic: vLLM systemd service automatically downloads model weights on first start.
# 2. Manual Download Links:
#    - Qwen2.5-0.5B-Instruct: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct
#    - Qwen2.5-3B-Instruct-AWQ: https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-AWQ
#    - Llama-3.2-1B-Instruct: https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct
# 3. Manual Download Command:
#    sudo HF_HOME=/var/lib/vllm/huggingface huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct
REFUSAL_PHRASES = [
    "i'm sorry", "im sorry", "cannot correct", "can't correct", "as an ai",
    "incomplete sentence", "please provide", "does not contain", "cannot fulfill",
    "here is the cleaned", "here is the corrected", "is there anything else", "how can i help"
]

def _has_valid_word_overlap(original: str, candidate: str, min_overlap: float = 0.35) -> bool:
    """Guardrail: ensures candidate output preserves speaker's core words (>35% overlap)."""
    orig_words = set(re.findall(r'\b\w+\b', original.lower()))
    cand_words = set(re.findall(r'\b\w+\b', candidate.lower()))
    if not orig_words or not cand_words:
        return True
    filler_words = {"um", "uh", "like", "so", "yeah", "actually", "scratch", "that"}
    clean_orig = orig_words - filler_words
    if not clean_orig:
        return True
    overlap_ratio = len(clean_orig.intersection(cand_words)) / float(len(clean_orig))
    return overlap_ratio >= min_overlap

def _is_valid_speech_rewrite(original: str, candidate: str) -> bool:
    """Guardrail to reject LLM refusal meta-chatter, text explosions, or hallucinated rewrites."""
    if not candidate:
        return False
    cand_lower = candidate.lower()
    for ref in REFUSAL_PHRASES:
        if ref in cand_lower:
            return False
    if len(candidate) > len(original) * 2.5 + 40:
        return False
    if not _has_valid_word_overlap(original, candidate):
        return False
    return True

_SLM_LAST_OFFLINE_CHECK = 0.0
_SLM_BASE_COOLDOWN = 5.0
_SLM_MAX_COOLDOWN = 120.0
_SLM_OFFLINE_STREAK = 0


def _slm_cooldown_remaining() -> float:
    """Seconds still to wait before retrying a recently-unreachable SLM endpoint."""
    if _SLM_OFFLINE_STREAK <= 0:
        return 0.0
    cooldown = min(_SLM_MAX_COOLDOWN, _SLM_BASE_COOLDOWN * (2 ** min(_SLM_OFFLINE_STREAK - 1, 5)))
    return max(0.0, cooldown - (time.time() - _SLM_LAST_OFFLINE_CHECK))


def process_slm_llm_rewrite(text: str, timeout_sec: float = None) -> str:
    """
    Passes speech transcript through local vLLM / SLM (Qwen2.5-0.5B / Llama-3.2-1B)
    to perform real-time speech self-correction and grammar polishing.
    """
    global _SLM_LAST_OFFLINE_CHECK, _SLM_OFFLINE_STREAK
    if not text or len(text.strip()) < 5 or os.environ.get("VT_ENABLE_SLM", "0") != "1":
        return text

    # Refuse a non-loopback endpoint *before* any connection (including the
    # /v1/models probe below) is attempted, so a misconfigured VT_VLLM_URL can
    # never leak a transcript. Loud on purpose: the message names the flag that
    # re-enables a deliberate remote setup.
    try:
        _slm_endpoint(VLLM_API_URL)
    except SlmRemoteRefused as exc:
        print(f"⛔ [vLLM SLM Pass] Refused: {exc} Using ASR text.")
        return text

    # Exponential back-off once the endpoint is known to be unreachable, so a
    # machine with no local vLLM never pays a per-utterance connect timeout.
    if _slm_cooldown_remaining() > 0:
        return text

    if timeout_sec is None:
        base_timeout = float(os.environ.get("VT_SLM_TIMEOUT", "2.5"))
        word_count = len(text.split())
        # Dynamic scaling: allow extra time for longer paragraph CPU inference
        timeout_sec = max(base_timeout, 1.5 + word_count * 0.05)

    model_name = os.environ.get("VT_SLM_MODEL", "")
    if not model_name:
        try:
            # Derived from the chat URL so that it follows VT_VLLM_URL. It used
            # to be hardcoded to port 8000, so discovery silently only ever worked
            # for the default endpoint and every other setup fell back to the name
            # below. OpenAI-compatible servers put /models beside /chat/completions.
            models_url = VLLM_API_URL.rsplit("/chat/completions", 1)[0] + "/models"
            req_mod = urllib.request.Request(models_url)
            with _VLLM_OPENER.open(req_mod, timeout=0.5) as resp_mod:
                mod_data = json.loads(resp_mod.read().decode('utf-8'))
                if mod_data.get("data"):
                    model_name = mod_data["data"][0]["id"]
        except Exception:
            pass
    if not model_name:
        model_name = "Qwen/Qwen2.5-1.5B-Instruct"
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an automated speech dictation text cleaner. "
                    "The input is raw speech-to-text output. "
                    "STRICT RULES:\n"
                    "1. Only remove verbal retractions (e.g. 'X... actually Y' -> 'Y') and hesitation fillers (um, uh).\n"
                    "2. Keep every spoken word verbatim; never rephrase, summarize, or add words.\n"
                    "3. NEVER invent new sentences, topics, or subjects not present in the input text.\n"
                    "4. NEVER output conversational replies, apologies, or meta-explanations.\n"
                    "5. NEVER add quotation marks, backticks, markdown, or code formatting of any kind.\n"
                    "6. NEVER add punctuation that was not spoken (no new question marks, periods, or commas).\n"
                    "7. Output ONLY the raw cleaned text enclosed inside <cleaned_text> tags, with no other text."
                )
            },
            {
                "role": "user",
                "content": f"<input_text>{text}</input_text>"
            }
        ],
        "temperature": 0.0,
        "max_tokens": 150
    }

    t0 = time.time()
    try:
        req = urllib.request.Request(
            VLLM_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with _VLLM_OPENER.open(req, timeout=timeout_sec) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            _SLM_OFFLINE_STREAK = 0
            clean_output = _sanitize_slm_output(text, res_data['choices'][0]['message']['content'])
            elapsed_ms = (time.time() - t0) * 1000

            # Apply guardrail: reject refusal meta-chatter, text explosions, or word-overlap failures
            if _is_valid_speech_rewrite(text, clean_output):
                print(f"🤖 [vLLM SLM Pass] Executed in {elapsed_ms:.1f}ms ({model_name}): '{text}' -> '{clean_output}'")
                return clean_output
            else:
                print("⚠️ [vLLM SLM Pass] Guardrail triggered (word-overlap or refusal failure): Bypassed -> Using ASR text")
    except Exception as e:
        _SLM_LAST_OFFLINE_CHECK = time.time()
        _SLM_OFFLINE_STREAK += 1
        print(f"⚠️ [vLLM SLM Pass] Offline/Bypassed ({e}): Using ASR text")

    return text

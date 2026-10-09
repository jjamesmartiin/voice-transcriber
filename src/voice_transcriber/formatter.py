"""Optional on-device text formatter.

The formatter rewrites a cleaned transcript into the shape a cloud dictation
product would return: casing fixed, false starts resolved, spoken lists turned
into real list markup, e-mail structure applied. It is the **only** component in
this codebase that can invent text, so it is wrapped in the strictest validation
in the codebase (:func:`validate`) and it is strictly optional.

Two invariants hold no matter what goes wrong:

* :func:`format_text` **never raises** and **never** returns text that failed
  validation. Any exception, timeout, missing model or invalid output returns
  the cleaned-but-unformatted text unchanged. The feature may fail; dictation
  may not.
* Importing this module loads no model and imports no backend. A model-free test
  run never pays for ``llama_cpp``.

Design and rationale: ``docs/plan-on-device-formatter.md``. The backend registry
mirrors :mod:`transcribe2`'s on purpose — hardwiring a single backend caused
problems there the first time, and the same fix applies here.
"""

from __future__ import annotations

import importlib
import re
import time
from typing import Any

# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

#: Every formatter backend: a name, and the module implementing it. The swap
#: point required by the plan is a *config value* resolved through this dict, so
#: a future in-house fine-tune is a new module plus one entry here.
BACKENDS: dict[str, str] = {
    "s1-mini": "voice_transcriber.formatters.s1_mini",       # bundled GGUF, default
    "llama-server": "voice_transcriber.formatters.llama_server",  # external endpoint
    "noop": "voice_transcriber.formatters.noop",             # identity, for tests
}

#: Backend used when none is named. Must be a key of :data:`BACKENDS`.
#: Until ``s1-mini`` lands (milestone C3) this resolves to a module that does not
#: exist yet, which :func:`load_backend` treats as "no formatting available" -
#: the correct fail-safe, not a crash.
DEFAULT_BACKEND = "s1-mini"

#: What a backend module must expose. Checked on first load rather than at
#: import time, so declaring a backend stays free and a half-added one degrades
#: to "no formatting" instead of breaking dictation.
REQUIRED_FUNCTIONS = ("format_text", "available", "warm")

_loaded: dict[str, Any] = {}


class UnknownBackendError(ValueError):
    """A config named a formatter backend that is not in :data:`BACKENDS`."""


def available_backends() -> list[str]:
    """Every backend name a config may use, sorted."""
    return sorted(BACKENDS)


def resolve_backend_name(name: str | None = None) -> str:
    """Validate ``name`` against the registry without importing anything."""
    resolved = str(name or DEFAULT_BACKEND).strip().lower()
    if resolved not in BACKENDS:
        raise UnknownBackendError(
            f"Unknown formatter backend {resolved!r}. "
            f"Available: {', '.join(available_backends())}"
        )
    return resolved


def load_backend(name: str | None = None) -> Any | None:
    """Import and validate a backend module, or ``None`` if it is unusable.

    Never raises. A missing module, a broken dependency (``llama_cpp`` absent on
    a machine that never enabled the formatter) or a module missing part of the
    contract all mean the same thing to a caller: there is nothing to format
    with, so the transcript passes through untouched.
    """
    try:
        resolved = resolve_backend_name(name)
    except UnknownBackendError:
        return None
    if resolved in _loaded:
        return _loaded[resolved]
    try:
        module = importlib.import_module(BACKENDS[resolved])
    except Exception:
        module = None
    if module is not None:
        missing = [fn for fn in REQUIRED_FUNCTIONS if not hasattr(module, fn)]
        if missing:
            module = None
    _loaded[resolved] = module
    return module


def reset_backend_cache() -> None:
    """Forget loaded backends. For tests, and for a config change at runtime."""
    _loaded.clear()


def warm(backend: str | None = None) -> bool:
    """Preload the backend so the first real utterance is not the slow one.

    Returns whether a usable backend is ready. Never raises: warming is an
    optimisation, and a failure to warm must not surface to the user.
    """
    module = load_backend(backend)
    if module is None:
        return False
    try:
        if not module.available():
            return False
        module.warm()
        return True
    except Exception:
        return False


def may_run(cleanup_mode: str) -> bool:
    """False when deterministic cleanup is off.

    ``cleanup_mode: off`` promises that nothing is deleted, and the formatter's
    whole contract is the deletion of fillers and false starts. Running it there
    would break that promise, so this is a hard gate rather than a preference.
    """
    return str(cleanup_mode or "").strip().lower() != "off"


# ---------------------------------------------------------------------------
# The exact integration contract (docs/plan-on-device-formatter.md sec 5.3)
# ---------------------------------------------------------------------------

#: Mandatory system prompt, verbatim from the model card. Getting this wording
#: wrong degrades the output; getting the control line wrong blanks it.
SYSTEM_PROMPT = (
    "You are a text normalizer for speech-to-text transcripts. The input begins "
    "with a control line specifying the styling, structure, and context settings; "
    "clean the transcript to match those settings and output only the cleaned text."
)

#: The rendered assistant prefix S1-mini was trained against. Only needed if a
#: prompt is ever assembled by hand instead of through the chat template.
ASSISTANT_PREFIX = "<|im_start|>assistant\n<think>\n\n</think>\n\n"

STYLES = ("casual", "semi-casual", "semi-formal", "formal")
STRUCTURES = ("prose", "lists")
CONTEXTS = ("general", "email")

DEFAULT_STYLE = "semi-formal"
DEFAULT_CONTEXT = "general"
DEFAULT_TIMEOUT_S = 3.0

#: Input token ceiling. Longer transcripts are chunked at sentence boundaries
#: rather than truncated.
MAX_INPUT_TOKENS = 1000


def structure_for_mode(structure_mode: str) -> str:
    """Map the existing ``structure_mode`` onto the model's structure axis.

    There is deliberately no fourth setting: ``structure_mode`` already means
    exactly this, and telling the model what shape to emit beats letting it emit
    bullets and stripping them afterwards.
    """
    return "prose" if str(structure_mode or "").strip().lower() == "off" else "lists"


def build_control_line(style: str, structure: str, context: str) -> str:
    """``[Styling: X] [Structure: Y] [Context: Z]`` - the exact trained shape."""
    return f"[Styling: {style}] [Structure: {structure}] [Context: {context}]"


def build_user_message(
    transcript: str, *, style: str, structure: str, context: str
) -> str:
    """Control line, a newline, then the raw transcript. Nothing else."""
    return f"{build_control_line(style, structure, context)}\n{transcript}"


def max_new_tokens(input_tokens: int) -> int:
    """The model's own recommended output ceiling.

    This bounds runaway generation *and* latency, and supersedes any generic
    length cap.
    """
    return int(1.3 * max(0, int(input_tokens))) + 32


def approx_token_count(text: str) -> int:
    """Whitespace token count, standing in for the real tokenizer.

    Conservative on purpose: this runs on the hot path and only needs to bound
    output, not measure it. Documented as an approximation so nobody mistakes it
    for a real tokenizer.
    """
    return len(re.findall(r"\S+", text or ""))


# ---------------------------------------------------------------------------
# Output validation - guardrails 1-5 from the plan
# ---------------------------------------------------------------------------

#: Transforms the model is allowed to apply, applied to *both* sides of every
#: comparison so they cancel out. Anything the model does beyond these shows up
#: as text that is not in the input, which is exactly what guardrail 2 rejects.
_CONTRACTIONS = {
    "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "can not", "won't": "will not", "isn't": "is not",
    "aren't": "are not", "wasn't": "was not", "weren't": "were not",
    "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
    "couldn't": "could not", "shouldn't": "should not", "wouldn't": "would not",
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "it's": "it is", "that's": "that is", "there's": "there is", "he's": "he is",
    "i've": "i have", "you've": "you have", "we've": "we have",
    "i'll": "i will", "you'll": "you will", "we'll": "we will",
    "i'd": "i would", "let's": "let us",
}

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}

#: Minimum fraction of the input's tokens an output must retain.
#:
#: Guardrail 1's ceiling catches runaway generation but **cannot** catch
#: summarisation, because a summary is shorter than its input, not longer - yet
#: the plan names summarisation as something guardrail 1 exists to prevent. This
#: is the missing half, and it is deliberately loose so that legitimate heavy
#: rewriting still passes: a resolved retraction drops ~27% of the tokens in the
#: reference fixture, so the floor sits well below that and only gross
#: summarisation is rejected.
_MIN_RETENTION = 0.4

_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(\S.*?)\s*$", re.MULTILINE)

#: Just the leading marker, for stripping structure before a content comparison.
_LIST_MARKER_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", re.MULTILINE)


def _strip_list_markers(text: str) -> str:
    """Remove structural list markers ahead of the content comparison.

    Converting "First, unpack the boxes." into "1. Unpack the boxes." is a
    *structure* transformation, which the model is explicitly asked for - the
    leading "1" is the list, not a word the user said. Bullets are harmless
    either way because "-" is not alphanumeric, but numbered markers would
    otherwise be read as invented content and reject a perfectly good list.
    """
    return _LIST_MARKER_RE.sub("", text or "")

_META_RE = re.compile(
    r"(?:"
    r"\bi(?:'m| am) sorry\b"
    r"|\bi apologi[sz]e\b"
    r"|\bas an ai\b"
    r"|\bi (?:can(?:'t| not)|cannot) (?:help|assist)\b"
    r"|here (?:is|are) the (?:cleaned|formatted|normalis|normaliz)"
    r"|^\s*(?:cleaned|formatted)\s+text\s*:"
    r"|</?cleaned_text>"
    r"|```"
    r"|\bcertainly\b\s*[,!]"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


def _canonical_tokens(text: str) -> list[str]:
    """Normalise away every transformation the model is allowed to make.

    Case, punctuation, contractions and number words are all folded so that
    "seven PM" and "7 pm" compare equal. Whatever survives is real content, and
    guardrail 2 requires all of it to be present in the input.
    """
    lowered = (text or "").lower().replace("\u2019", "'")
    for contraction, expansion in _CONTRACTIONS.items():
        lowered = lowered.replace(contraction, expansion)
    return [_NUMBER_WORDS.get(tok, tok) for tok in re.findall(r"[a-z0-9]+", lowered)]


def _contains_subsequence(haystack: list[str], needle: list[str]) -> bool:
    """Is ``needle`` a contiguous run inside ``haystack``?"""
    if not needle:
        return True
    if len(needle) > len(haystack):
        return False
    first = needle[0]
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start] == first and haystack[start:start + len(needle)] == needle:
            return True
    return False


def _filler_regex() -> re.Pattern[str]:
    """The app's filler list, so the two cannot disagree about what a filler is."""
    try:
        from voice_transcriber.post_processor import FILLER_WORDS_REGEX
        return FILLER_WORDS_REGEX
    except Exception:  # pragma: no cover - standalone use
        return re.compile(r"\s*\b(?:um|uh|er|ah)\b\s*", re.IGNORECASE)


def has_content_words(text: str) -> bool:
    """True if anything survives filler removal. Drives guardrail 5."""
    return bool(_canonical_tokens(_filler_regex().sub(" ", text or "")))


def validate(original: str, candidate: str) -> str | None:
    """Check a candidate against every guardrail.

    Returns ``None`` when the candidate is usable, or a human-readable reason
    when it must be rejected. A reason is a bug report about the prompt, not
    something the user should ever see - the caller falls back silently.
    """
    if not isinstance(candidate, str):
        return "backend returned a non-string"

    # Guardrail 4: refusal / meta commentary / wrapper leakage. Checked first so
    # a refusal is reported as a refusal rather than as invented content.
    meta = _META_RE.search(candidate)
    if meta:
        return f"output looks like meta commentary ({meta.group(0)!r})"

    # Guardrail 5: an empty result is correct only if the input had no content.
    if not candidate.strip():
        if has_content_words(original):
            return "output is empty but the input had content words"
        return None

    # Guardrail 1: the model's own output ceiling.
    limit = max_new_tokens(approx_token_count(original))
    if approx_token_count(candidate) > limit:
        return f"output exceeds the {limit}-token ceiling"

    # Guardrail 1b: the retention floor. See _MIN_RETENTION - the ceiling above
    # catches expansion, this catches the summarisation the plan also names.
    input_content = _canonical_tokens(original)
    if input_content:
        retained = len(_canonical_tokens(_strip_list_markers(candidate))) / len(input_content)
        if retained < _MIN_RETENTION:
            return (
                f"output retained only {retained:.0%} of the input "
                f"(floor {_MIN_RETENTION:.0%}) - looks like a summary"
            )

    # Guardrail 2: no new content. Every surviving token must come from the
    # input, after the allowed transformations are folded away on both sides.
    input_tokens = set(_canonical_tokens(original))
    candidate_tokens = _canonical_tokens(_strip_list_markers(candidate))
    invented = [t for t in candidate_tokens if t not in input_tokens]
    if invented:
        seen: list[str] = []
        for token in invented:
            if token not in seen:
                seen.append(token)
        return f"output contains text absent from the input: {', '.join(seen[:5])}"

    # Guardrail 3: a list item the user never said is the failure mode that
    # would destroy trust in the feature, so items are checked verbatim.
    items = _LIST_ITEM_RE.findall(candidate)
    if items:
        haystack = _canonical_tokens(original)
        for item in items:
            if not _contains_subsequence(haystack, _canonical_tokens(item)):
                return f"list item does not appear verbatim in the input: {item!r}"

    return None


# ---------------------------------------------------------------------------
# The only entry point
# ---------------------------------------------------------------------------

def format_text(
    text: str,
    *,
    style: str = DEFAULT_STYLE,
    structure: str = "prose",
    context: str = DEFAULT_CONTEXT,
    backend: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """Format ``text``, or return it unchanged. Never raises.

    Guardrail 7 in one function: there is no error path out of here. Every
    failure - unknown backend, missing weights, exception, timeout, invalid
    output - returns the input, which has already been through deterministic
    cleanup and is therefore always something the user is happy to receive.
    """
    original = text if isinstance(text, str) else ""
    if not original.strip():
        return original

    try:
        module = load_backend(backend)
        if module is None:
            return original
        if not module.available():
            return original

        started = time.monotonic()
        candidate = module.format_text(
            original,
            style=style,
            structure=structure,
            context=context,
            timeout_s=timeout_s,
        )
        elapsed = time.monotonic() - started

        # Guardrail 6 backstop. The in-backend deadline (a StoppingCriteria for
        # llama.cpp) is the primary mechanism; this catches a backend that
        # ignored its budget and returned late.
        if elapsed > timeout_s:
            return original

        if validate(original, candidate) is not None:
            return original
        return candidate
    except Exception:
        return original

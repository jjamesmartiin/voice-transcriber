#!/usr/bin/env python3
"""
High-Speed Speech Transcription Post-Processor.
Fixes common ASR artifacts, disfluencies, and punctuation errors:
- False period boundary splitting before conjunctions (". and" -> ", and")
- Stuttered word repetitions across punctuation ("about. about" -> "about", "a. a" -> "a")
- Dangling prepositions, articles, and determiners ("put a. period" -> "put a period")
- Discourse marker over-punctuation ("So. I think" -> "So, I think")
- Incomplete clause links ("thing was. something" -> "thing was something")
- ASR hallucination scrubbing ("Thanks for watching", trailing ". you", ". bye")
"""

import os
import re
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

# Precompiled hallucination patterns
HALLUCINATION_PATTERNS = [
    re.compile(r"\bthanks?\s+(for\s+)?watching[.!?,]*\b", re.IGNORECASE),
    re.compile(r"\bthank\s+you\s+for\s+watching[.!?,]*\b", re.IGNORECASE),
    re.compile(r"\bsubtitles\s+by\s+.*$", re.IGNORECASE),
    re.compile(r"\bplease\s+subscribe[.!?,]*\b", re.IGNORECASE),
    re.compile(r"={2,}[^=\n]+={2,}[.?!]*", re.IGNORECASE),
]

# Standalone single-word noise hallucinations on short audio clips
STANDALONE_SHORT_HALLUCINATIONS = re.compile(
    r"^\s*(you|bye|thank\s+you|thanks|subtitles|shh+|ptl)\s*[.?!]*$", re.IGNORECASE
)

# Words that can never grammatically end an English sentence / clause
DANGLING_WORDS_REGEX = re.compile(
    r"\b("
    r"a|an|the|my|your|his|her|our|their|its|this|that|these|those|"
    r"of|to|in|for|with|on|at|from|by|about|into|through|during|below|between|under|without|"
    r"and|or|but|because|if|while|since|until|unless|"
    r"very|too|quite|really|such|more|less|most|least|"
    r"two|three|four|five|several|multiple|few|many|some|another|each|every|"
    r"different|similar|same|other|next|previous|main|"
    r"is|are|was|were|be|been|being|have|has|had|can|could|would|should|shall|will|might|must"
    r")\s*[.?!]\s+([a-zA-Z])",
    re.IGNORECASE
)

# Common conversational discourse starters
DISCOURSE_STARTERS_REGEX = re.compile(
    r"(^|(?<=[.?!]\s))(yeah|yes|no|so|well|okay|ok|sure|right|actually|meanwhile|anyway|anyways)\s*[.]\s+([a-zA-Z])",
    re.IGNORECASE
)

# Repeated words across period/dash (e.g. "about. about", "might. Might", "a. a")
# Note: we do NOT collapse words across commas (e.g. "two, two", "no, no", "really, really")
# because those are natural spoken repetitions or number enumerations.
STUTTER_PUNCT_REGEX = re.compile(
    r"\b([a-zA-Z]+)\s*[.-]+\s+(?i:\1)\b"
)

# High-confidence repeated words without punctuation (e.g. "the the", "a a", "in in", "about about", "might might")
STUTTER_DIRECT_REGEX = re.compile(
    r"\b("
    r"a|an|the|my|your|our|their|this|that|"
    r"in|on|at|to|for|from|with|by|of|about|into|"
    r"might|can|could|will|would|should|must|is|was|are|were|"
    r"and|or|but|so|because|if|we|i|you|he|she|they"
    r")\s+\1\b",
    re.IGNORECASE
)

_STUTTER_DIRECT_WORDS = frozenset({
    "a", "an", "the", "my", "your", "our", "their", "this", "that",
    "in", "on", "at", "to", "for", "from", "with", "by", "of", "about", "into",
    "might", "can", "could", "will", "would", "should", "must", "is", "was", "are", "were",
    "and", "or", "but", "so", "because", "if", "we", "i", "you", "he", "she", "they"
})

STUTTER_PROTECTED_WORDS = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion",
    "no", "yes", "really", "very", "bye", "hear", "now", "never", "again", "too", "so", "oh"
})

def _collapse_stutter_punct(m: re.Match) -> str:
    w = m.group(1)
    if w.lower() in STUTTER_PROTECTED_WORDS:
        return m.group(0)
    return w

def _check_stutters(text_lower: str) -> tuple[bool, bool]:
    has_punct_cand = False
    has_direct = False
    prev = ""
    for w in text_lower.split():
        w_clean = w.strip(".,!?:;\"'()[]{}")
        if w_clean and w_clean == prev:
            if w_clean not in STUTTER_PROTECTED_WORDS:
                has_punct_cand = True
            if w_clean in _STUTTER_DIRECT_WORDS:
                has_direct = True
        prev = w_clean
    return has_punct_cand, has_direct

def _has_stutter_direct(text_lower: str) -> bool:
    _, has_direct = _check_stutters(text_lower)
    return has_direct

# Preposition compound stutters (e.g. "into in to" -> "into", "in to into" -> "into", "onto on to" -> "onto")
PREPOSITION_COMPOUND_STUTTER_REGEX = re.compile(
    r"\b(into\s+in\s+to|in\s+to\s+into|onto\s+on\s+to|on\s+to\s+onto)\b",
    re.IGNORECASE
)

# Coordinating conjunctions following a period
COORD_CONJUNCTIONS_REGEX = re.compile(
    r"[.]\s+(and|or|but|so|yet|nor)\b",
    re.IGNORECASE
)

# Mid-phrase spurious question mark followed by lowercase continuation (e.g. "the right? stuff" -> "the right stuff")
MID_PHRASE_QUESTION_MARK_REGEX = re.compile(
    r"\b([a-zA-Z0-9]+)\s*\?\s+([a-z][a-zA-Z0-9_-]*)\b"
)

# Spoken Unix paths, IP addresses and CIDR subnets.
#   "slash etc slash nixos"                 -> "/etc/nixos"
#   "10 dot 0 dot 0 dot 0 slash 24"          -> "10.0.0.0/24"
# NOTE: the path pattern must NOT consume trailing whitespace, otherwise the
# next word is glued on ("... slash nixos now" -> "/etc/nixosnow").
SPOKEN_PATH_REGEX = re.compile(
    r'\b(?:slash\s+[a-zA-Z0-9_.-]+)(?:\s+slash\s+[a-zA-Z0-9_.-]+)+',
    re.IGNORECASE,
)
SPOKEN_IP_REGEX = re.compile(
    r'\b(\d{1,3}(?:\s+dot\s+\d{1,3})+)(?:\s+slash\s+(\d{1,2}))?\b',
    re.IGNORECASE,
)
# Bare CIDR suffix already attached to a digit ("10.0.0.0 slash 24").
SPOKEN_SUBNET_REGEX = re.compile(r'(?<=\d)\s+slash\s+(\d{1,2})\b', re.IGNORECASE)


def _path_repl(m: re.Match) -> str:
    segments = re.findall(r'slash\s+([a-zA-Z0-9_.-]+)', m.group(0), flags=re.IGNORECASE)
    # Path segments are case-sensitive and dictated lowercase; the dictionary has
    # already run, so undo any proper-noun capitalisation it applied ("NixOS" ->
    # "/etc/nixos"). Put a mixed-case path in the dictionary if you need it.
    return "/" + "/".join(s.lower() for s in segments)


def _ip_repl(m: re.Match) -> str:
    ip = re.sub(r'\s+dot\s+', '.', m.group(1), flags=re.IGNORECASE)
    if m.group(2):
        return f"{ip}/{m.group(2)}"
    return ip


def clean_spoken_paths(text: str) -> str:
    """Converts spoken Unix paths, IP addresses and CIDR subnets to symbols."""
    if not text:
        return text
    low = text.lower()
    if "slash" not in low and " dot " not in low:
        return text
    text = SPOKEN_IP_REGEX.sub(_ip_repl, text)
    text = SPOKEN_SUBNET_REGEX.sub(r'/\1', text)
    text = SPOKEN_PATH_REGEX.sub(_path_repl, text)
    return text

# ASR phonetic mis-hearings of the "AI" acronym (e.g. "a eyes" -> "AI").
# ASR models sometimes transcribe spoken "AI" as "a eyes" / "an eyes".
# Both are ALWAYS ungrammatical English ("a"/"an" can never precede the plural
# "eyes"), so they're safe to collapse back to the acronym. "an eye" is NOT
# included (valid English: "an eye for an eye"), nor is "a eye" (many speakers
# casually say "a eye" meaning "an eye").
AI_MISHEARINGS_REGEX = re.compile(
    r"\b(?:a|an)\s+eyes\b",
    re.IGNORECASE
)

# ASR phonetic mis-hearings of "addressing" (e.g. "needs a dressing" / "needs a. dressing" -> "needs addressing")
ADDRESSING_MISHEARINGS_REGEX = re.compile(
    r"\b(needs?|requires?|worth|without|before|after|about|by|for|start(?:ed|s|ing)?|stop(?:ped|s|ing)?)\s+(?:a\s*[.]\s*|a\s+)dressing\b",
    re.IGNORECASE
)

# Stray single-consonant microphone click/onset clipping at start of utterance (e.g. "t needs" -> "needs")
LEADING_STRAY_T_REGEX = re.compile(
    r"^\s*(?:t|\x27t)\s+(needs\b)",
    re.IGNORECASE
)

# Common technical acronyms & proper nouns to preserve casing mid-sentence
TECHNICAL_ACRONYMS_AND_PROPER_NOUNS = {
    "I", "vLLM", "NixOS", "PyTorch", "Python", "GitHub", "Git", "WSL", "WSLg",
    "CPU", "GPU", "RAM", "VRAM", "HDMI", "ALSA", "PipeWire", "PortAudio",
    "REST", "API", "LLM", "SLM", "ASR", "JSON", "YAML", "ONNX", "VM", "Cohere",
    "Whisper", "Qwen", "Linux", "Windows", "CUDA", "ID", "UI", "TUI", "CLI",
    "OK", "IP", "URL", "HTTP", "HTTPS", "SSD", "NVMe", "USB", "PCIe", "BIOS",
    "JK", "DF", "Deepseek", "DeepSeek"
}

MID_SENTENCE_CAP_REGEX = re.compile(r"(?<![.!?\n])\s+([A-Z][a-zA-Z0-9_-]+)")
_UPPERCASE_CHAR_REGEX = re.compile(r"[A-Z]")
MID_SENTENCE_BOUNDARY_REGEX = re.compile(r"[.?!]\s+[a-zA-Z]")

# Words after which a capitalized word is treated as a proper noun (name, place, day)
# and exempted from mid-sentence decapitalization, e.g. "Send it to Alice", "on Wednesday".
PROPER_NOUN_PRECEDERS = {
    "to", "for", "with", "at", "in", "on", "about", "from", "by", "of",
}

# Common English function/discourse words that ASR sometimes over-capitalizes mid-sentence
# (e.g. "to Like quit", "in This section"). These are NEVER treated as proper nouns,
# even after a preposition. Day/month names are intentionally NOT included so
# "on Monday" / "in May" keep their capitalization.
COMMON_MID_SENTENCE_WORDS = {
    "like", "this", "that", "these", "those", "then", "there", "their", "they",
    "when", "where", "why", "how", "who", "which", "what", "if", "and", "but",
    "or", "so", "for", "from", "with", "about", "because", "though", "although",
    "since", "while", "during", "after", "before", "above", "below", "over",
    "under", "between", "into", "onto", "upon", "within", "without", "against",
    "through", "across", "along", "behind", "beyond", "near", "off", "out", "up",
    "down", "not", "no", "yes", "very", "really", "just", "only", "also", "now",
    "here", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "have", "has", "had", "will", "would", "can", "could", "should",
    "might", "must", "shall", "want", "need", "let", "make", "get", "take", "go",
    "come", "see", "know", "think", "say", "tell", "use", "try", "look", "put",
    "give", "find", "work", "play", "start", "stop", "keep", "help", "check", "fix",
}

def normalize_mid_sentence_casing(text: str) -> str:
    """
    Decapitalizes words appearing mid-sentence without preceding sentence-ending punctuation,
    unless the word is a recognized acronym or proper noun.
    """
    if not text or not _UPPERCASE_CHAR_REGEX.search(text, 1):
        return text

    def _replace_mid_sentence_cap(m):
        word = m.group(1)
        if word in TECHNICAL_ACRONYMS_AND_PROPER_NOUNS or word.isupper() or len(word) == 1:
            return m.group(0)
        # Preserve plural acronyms mid-sentence ("LLMs", "VMs", "GPUs", "APIs", "SSDs"):
        # all-caps stem + trailing plural 's'. The base-length guard keeps "Is"/"As"/"Us"
        # from being exempted (they should still decapitalize to is/as/us).
        if len(word) > 2 and word.endswith("s") and word[:-1].isupper():
            return m.group(0)
        # Preserve proper nouns (names/places/days) that follow a preposition
        # (e.g. "to Alice", "on Wednesday"), unless it is a common English word
        # that ASR over-capitalized (e.g. "to Like", "on This")
        start_idx = m.start()
        idx = start_idx - 1
        while idx >= 0 and text[idx] in " \t\r\n":
            idx -= 1
        end_prev = idx + 1
        while idx >= 0 and text[idx] not in " \t\r\n":
            idx -= 1
        start_prev = idx + 1
        prev_word = text[start_prev:end_prev].rstrip(".,;:!?") if end_prev > start_prev else ""
        if prev_word.lower() in PROPER_NOUN_PRECEDERS and word.lower() not in COMMON_MID_SENTENCE_WORDS:
            return m.group(0)
        lowercased = word[0].lower() + word[1:]
        return " " + lowercased

    return MID_SENTENCE_CAP_REGEX.sub(_replace_mid_sentence_cap, text)

# Subordinating conjunctions / relative pronouns following a period
SUBORD_CONJUNCTIONS_REGEX = re.compile(
    r"[.]\s+(because|which|that|though|although)\b",
    re.IGNORECASE
)

# Linking verbs followed by lowercase continuation
LINKING_VERB_LOWER_REGEX = re.compile(
    r"\b(is|are|was|were|am|be|been|being)\s*[.]\s+([a-z])"
)

# Lowercase continuation after a period
LOWERCASE_AFTER_PERIOD_REGEX = re.compile(
    r"[.]\s+([a-z])"
)

# Trailing muttered self-corrections (oops, whoops, nevermind) at end of dictation
# Standalone hesitation filler words (um, uh, er, ah)
FILLER_WORDS_REGEX = re.compile(
    r"\s*\b(?:um|uh|er|ah)\b\s*",
    re.IGNORECASE
)

TRAILING_MUTTERINGS_REGEX = re.compile(
    r"([.?!,;:]|\s)\s*(?:oops|whoops|oopsy|whoopsy|oop|opps|nevermind|never\s+mind)[.!?,;:]*\s*$",
    re.IGNORECASE
)

STANDALONE_MUTTERINGS_REGEX = re.compile(
    r"^\s*(?:oops|whoops|oopsy|whoopsy|oop|opps|nevermind|never\s+mind)[.!?,;:]*\s*$",
    re.IGNORECASE
)

# Verbal retractions and self-corrections (e.g. "5 PM... actually 6 PM", "John... I mean Alice", "scratch that")
RETRACTION_REPLACEMENT_PATTERNS = [
    # "scratch that" / "strike that" / "never mind that" at end of phrase
    re.compile(r"(?:[,.]*\s*)(?:scratch\s+that|strike\s+that|never\s+mind\s+that)[.!?,;:]*\s*$", re.IGNORECASE),
    # "X... actually Y" / "X... make that Y" / "X... no wait Y" / "X... I mean Y" / "X... or rather Y"
    re.compile(r"(?:\b(at|in|on|to|for|by|from|with|of|about)\s+)?\b([a-zA-Z0-9$%.\:]+(?:\s+[a-zA-Z0-9$%.\:]+){0,1})\s*[,.]*\s*(?:actually|make\s+that|no\s+wait|I\s+mean|or\s+rather)\s+([a-zA-Z0-9$%.\:]+(?:\s+[a-zA-Z0-9$%.\:]+){0,2})\b", re.IGNORECASE),
]

_RETRACTION_TRIGGER_0 = ("scratch", "strike", "never")
_RETRACTION_TRIGGER_1 = ("actually", "make that", "no wait", "mean", "rather")

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


def process_verbal_retractions(text: str, text_lower: str | None = None) -> str:
    """
    Applies zero-latency verbal self-correction parsing:
    Replaces retracted phrases ('5 PM... actually 6 PM' -> '6 PM')
    and handles voice deletions ('scratch that').
    """
    if not text:
        return ""
        
    if text_lower is None:
        text_lower = text.lower()
    has_0 = ("scratch" in text_lower or "strike" in text_lower or "never" in text_lower)
    has_1 = ("actually" in text_lower or "mean" in text_lower or "rather" in text_lower or "make that" in text_lower or "no wait" in text_lower)
    if not has_0 and not has_1:
        return text

    cleaned = text
    # 1. Handle "scratch that" tail deletion
    if has_0 and RETRACTION_REPLACEMENT_PATTERNS[0].search(cleaned):
        cleaned = RETRACTION_REPLACEMENT_PATTERNS[0].sub("", cleaned)
    
    # 2. Handle verbal replacements ("X... actually Y", "X... I mean Y")
    if has_1 and RETRACTION_REPLACEMENT_PATTERNS[1].search(cleaned):
        def _replace_retraction(m):
            prep = m.group(1)
            target = m.group(3)
            if prep and not re.match(r"^(?:at|in|on|to|for|by|from|with|of|about)\b", target, re.I):
                return f"{prep} {target}"
            return target
            
        cleaned = RETRACTION_REPLACEMENT_PATTERNS[1].sub(_replace_retraction, cleaned)
    return cleaned.strip()

VLLM_API_URL = os.environ.get("VT_VLLM_URL", "http://localhost:8000/v1/chat/completions")

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
            req_mod = urllib.request.Request("http://localhost:8000/v1/models")
            with urllib.request.urlopen(req_mod, timeout=0.5) as resp_mod:
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
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            _SLM_OFFLINE_STREAK = 0
            clean_output = _sanitize_slm_output(text, res_data['choices'][0]['message']['content'])
            elapsed_ms = (time.time() - t0) * 1000
            
            # Apply guardrail: reject refusal meta-chatter, text explosions, or word-overlap failures
            if _is_valid_speech_rewrite(text, clean_output):
                print(f"🤖 [vLLM SLM Pass] Executed in {elapsed_ms:.1f}ms ({model_name}): '{text}' -> '{clean_output}'")
                return clean_output
            else:
                print(f"⚠️ [vLLM SLM Pass] Guardrail triggered (word-overlap or refusal failure): Bypassed -> Using ASR text")
    except Exception as e:
        _SLM_LAST_OFFLINE_CHECK = time.time()
        _SLM_OFFLINE_STREAK += 1
        print(f"⚠️ [vLLM SLM Pass] Offline/Bypassed ({e}): Using ASR text")
        
    return text

def process_slm_llm_stream_concat(prev_text: str, new_chunk: str, timeout_sec: float = None) -> str:
    """
    Real-time streaming LLM fusion: smooths and concatenates incoming ASR micro-batch chunks
    onto the rolling transcript context while the user is speaking.
    """
    if not prev_text:
        return new_chunk or ""
    if not new_chunk:
        return prev_text
        
    if os.environ.get("VT_ENABLE_SLM", "0") != "1":
        return f"{prev_text} {new_chunk}".strip()

    if timeout_sec is None:
        timeout_sec = float(os.environ.get("VT_SLM_TIMEOUT", "1.5"))
        
    model_name = os.environ.get("VT_SLM_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "You are an automated speech dictation stream concater.\nSTRICT OPERATIONAL CONTRACT:\n1. Smoothly join the new incoming audio chunk onto the previous transcript with natural word spacing.\n2. DO NOT delete, summarize, or rephrase valid words from either text.\n3. NEVER output conversational responses or meta-commentary.\n4. NEVER add quotation marks, backticks, markdown, or code formatting of any kind.\n5. NEVER add punctuation that was not spoken.\n6. Output ONLY the merged transcript."
            },
            {
                "role": "user",
                "content": f"Previous Transcript: '{prev_text}'\nNew Chunk: '{new_chunk}'"
            }
        ],
        "temperature": 0.0,
        "max_tokens": 200
    }
    
    t0 = time.time()
    try:
        req = urllib.request.Request(
            VLLM_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            merged_output = _sanitize_slm_output(f"{prev_text} {new_chunk}", res_data['choices'][0]['message']['content'])
            elapsed_ms = (time.time() - t0) * 1000
            
            if _is_valid_speech_rewrite(f"{prev_text} {new_chunk}", merged_output):
                print(f"🤖 [vLLM Stream Merge] Executed in {elapsed_ms:.1f}ms: + '{new_chunk}' -> '{merged_output}'")
                return merged_output
            else:
                print(f"⚠️ [vLLM Stream Merge] Guardrail triggered: Using standard concat")
    except Exception as e:
        print(f"⚠️ [vLLM Stream Merge] Offline/Bypassed ({e}): Using standard concat")
        
    return f"{prev_text} {new_chunk}".strip()

def _preserve_i_casing(char: str, text: str = "", pos: int = 0) -> str:
    """Preserves uppercase 'I' pronoun while lowercasing continuation words."""
    if not char:
        return ""
    if char.upper() == 'I':
        rest = text[pos:] if pos < len(text) else ""
        if not rest or rest[0] in " '.,!?;:\n\t":
            return 'I'
    return char.lower()


# ---------------------------------------------------------------------------
# Number words -> digits conversion
# ---------------------------------------------------------------------------
# Gated by VT_NUMBER_DIGITS (default "1"; set to "0" to keep number words).
# Also toggleable at runtime via set_number_digits_enabled() (driven by the
# t2.NUMBER_DIGITS config setting and the TUI 'n' hotkey).
# Converts spoken numbers into actual digits, e.g.:
#   "six seven zero six seven zero six nine nine six" -> "7606706996"  (phone/digit string)
#   "twenty five" -> "25", "one hundred and fifty" -> "150", "two thousand twenty four" -> "2024"
#   "twenty first" -> "21st", "fifth" -> "5th", "three point one four" -> "3.14"
#   "fifty percent" -> "50%", "five pm" -> "5 PM", "nineteen eighty five" -> "1985"

_NUMBER_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_NUMBER_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_SCALES = {
    "hundred": 100, "thousand": 1000, "million": 1_000_000,
    "billion": 1_000_000_000, "trillion": 1_000_000_000_000,
}
_ORDINAL_TO_CARDINAL = {
    "first": "one", "second": "two", "third": "three", "fourth": "four",
    "fifth": "five", "sixth": "six", "seventh": "seven", "eighth": "eight",
    "ninth": "nine", "tenth": "ten", "eleventh": "eleven", "twelfth": "twelve",
    "thirteenth": "thirteen", "fourteenth": "fourteen", "fifteenth": "fifteen",
    "sixteenth": "sixteen", "seventeenth": "seventeen", "eighteenth": "eighteen",
    "nineteenth": "nineteen", "twentieth": "twenty", "thirtieth": "thirty",
    "fortieth": "forty", "fiftieth": "fifty", "sixtieth": "sixty",
    "seventieth": "seventy", "eightieth": "eighty", "ninetieth": "ninety",
    "hundredth": "hundred", "thousandth": "thousand", "millionth": "million",
    "billionth": "billion",
}
_ORDINAL_VALUE = {k: i for i, k in enumerate(["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth"], start=1)}
_ORDINAL_VALUE.update({k: v for k, v in [("twentieth", 20), ("thirtieth", 30), ("fortieth", 40), ("fiftieth", 50), ("sixtieth", 60), ("seventieth", 70), ("eightieth", 80), ("ninetieth", 90), ("hundredth", 100), ("thousandth", 1000), ("millionth", 1_000_000), ("billionth", 1_000_000_000)]})

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_DIGIT_WORD_ALT = "|".join(_DIGIT_WORDS)
_DIGIT_TOKEN = rf"(?:(?:double|triple)\s+)?(?:\b(?:{_DIGIT_WORD_ALT})\b)"
# Two or more consecutive digit words: "six seven zero" / "double oh seven" / "one two three"
_DIGIT_STRING_REGEX = re.compile(rf"\b(?:{_DIGIT_TOKEN})(?:\s+(?:{_DIGIT_TOKEN}))+\b", re.IGNORECASE)

class _TrieNode:
    __slots__ = ("children", "is_end")
    def __init__(self):
        self.children = {}
        self.is_end = False

def _build_trie_regex(words: list[str]) -> str:
    """Builds a compact nested alternation regex from a list of words/phrases."""
    root = _TrieNode()
    for w in words:
        curr = root
        for char in w:
            curr = curr.children.setdefault(char, _TrieNode())
        curr.is_end = True

    def _node_to_regex(node: _TrieNode) -> str:
        if not node.children:
            return ""
        alts = []
        for char, child in sorted(node.children.items()):
            sub = _node_to_regex(child)
            esc = re.escape(char)
            if child.is_end and sub:
                alts.append(f"{esc}(?:{sub})?")
            elif child.is_end:
                alts.append(esc)
            else:
                alts.append(f"{esc}{sub}")
        if len(alts) == 1:
            return alts[0]
        return "(?:" + "|".join(alts) + ")"

    return _node_to_regex(root)

_NUMBER_WORD_LIST = [
    "zero", "oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion",
    "trillion", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
    "eighteenth", "nineteen", "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth",
    "seventieth", "eightieth", "ninetieth", "hundredth", "thousandth", "millionth", "billionth",
    "point", "percent", "o'clock", "am", "pm", "a.m.", "p.m.", "a m", "p m", "double", "triple"
]
_NUMBER_WORD = _build_trie_regex(sorted(_NUMBER_WORD_LIST, key=len, reverse=True))
_NUMBER_PHRASE_REGEX = re.compile(
    r"\b(?:a\s+)?(?:" + _NUMBER_WORD + r")(?:(?:[\s\-]+|\s+and\s+)(?:" + _NUMBER_WORD + r"))*\b",
    re.IGNORECASE,
)

_ALL_NUMBER_WORDS_SET = frozenset(
    list(_NUMBER_ONES.keys()) +
    list(_NUMBER_TENS.keys()) +
    list(_NUMBER_SCALES.keys()) +
    list(_ORDINAL_TO_CARDINAL.keys()) +
    ["oh", "point", "percent", "o'clock", "am", "pm", "double", "triple", "a.m.", "p.m."]
)

# Contexts where a standalone "one" is a pronoun/idiom, not a number
_ONE_BEFORE_PROTECT = {"the", "this", "that", "no", "any", "each", "every", "first", "last",
                       "only", "another", "other", "some", "big", "little", "small", "number"}
_ONE_AFTER_PROTECT = {"of", "thing", "more", "way", "day", "time", "side", "another", "hand",
                      "eye", "ear", "one"}


def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _parse_cardinal(tokens):
    """Parse a list of cardinal word tokens into an int, or None if invalid.
    Rejects invalid orderings (e.g. 'nine thirty' is a clock time, not a cardinal)."""
    if not tokens:
        return None
    total = 0
    current = 0
    last_was_ones = False
    for tok in tokens:
        if tok == "and":
            last_was_ones = False
            continue
        v_one = _NUMBER_ONES.get(tok)
        if v_one is not None:
            if last_was_ones:
                return None  # 'one two' style digit string, not a cardinal
            current += v_one
            last_was_ones = True
            continue
        v_ten = _NUMBER_TENS.get(tok)
        if v_ten is not None:
            if last_was_ones:
                return None  # 'nine thirty' is not a valid cardinal
            current += v_ten
            last_was_ones = False
            continue
        v_scale = _NUMBER_SCALES.get(tok)
        if v_scale is not None:
            if v_scale >= 1000:
                total += (current or 1) * v_scale
                current = 0
            else:
                current = (current or 1) * v_scale
            last_was_ones = False
            continue
        return None
    return total + current


def _parse_ordinal(tokens):
    """
    Parse an ordinal phrase: 'fifth' -> (5, 'th'); 'twenty first' -> (21, 'st');
    'one hundred and fifth' -> (105, 'th'). Returns (number, suffix) or None.
    The tokens before the final ordinal must form a valid tens/scale construction
    (e.g. 'twenty first', 'one hundred and first') so 'one second' stays as-is.
    """
    if not tokens:
        return None
    last = tokens[-1].lower()
    v_ord = _ORDINAL_VALUE.get(last)
    if v_ord is None:
        return None
    card_tokens = []
    for i, tok in enumerate(tokens):
        tl = tok.lower()
        if i == len(tokens) - 1:
            card_tokens.append(_ORDINAL_TO_CARDINAL[tl])
        elif tl == "and":
            card_tokens.append(tl)
        elif tl in _NUMBER_TENS or tl in _NUMBER_SCALES:
            card_tokens.append(tl)
        elif tl in _NUMBER_ONES and i + 1 < len(tokens) and tokens[i + 1].lower() in _NUMBER_SCALES:
            # "one" allowed only when building a scale like "one hundred"
            card_tokens.append(tl)
        else:
            return None
    n = _parse_cardinal(card_tokens)
    if n is None:
        return None
    return n, _ordinal_suffix(v_ord)


_YEAR_REGEX_1 = re.compile(
    r"^(nineteen|twenty)\s+(ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:\s+(one|two|three|four|five|six|seven|eight|nine))?$"
)
_YEAR_REGEX_2 = re.compile(r"^two\s+thousand(?:\s+and)?(?:\s+(.+))?$")

def _parse_year(phrase_lower: str):
    """Recognize common spoken-year patterns, e.g. 'nineteen eighty five' -> 1985."""
    if not (phrase_lower.startswith("nineteen ") or phrase_lower.startswith("twenty ") or phrase_lower.startswith("two thousand")):
        return None
    m = _YEAR_REGEX_1.match(phrase_lower)
    if m:
        century = 1900 if m.group(1) == "nineteen" else 2000
        rest = _NUMBER_TENS.get(m.group(2), 0) or _NUMBER_ONES.get(m.group(2), 0)
        if m.group(3):
            rest += _NUMBER_ONES[m.group(3)]
        return century + rest
    m2 = _YEAR_REGEX_2.match(phrase_lower)
    if m2:
        if not m2.group(1):
            return 2000
        n = _parse_cardinal(m2.group(1).split())
        if n is not None and n < 100:
            return 2000 + n
    return None


def _expand_digit_string(m):
    """Convert a matched digit-word string (e.g. 'double oh seven') into digits."""
    parts = m.group(0).replace("-", " ").split()
    out = []
    i = 0
    while i < len(parts):
        p = parts[i].lower()
        if p in ("double", "triple"):
            mult = 2 if p == "double" else 3
            i += 1
            if i < len(parts):
                out.append(_DIGIT_WORDS.get(parts[i].lower(), "") * mult)
        else:
            out.append(_DIGIT_WORDS.get(p, ""))
        i += 1
    return "".join(out)


def _is_pure_digit_string(tokens):
    """True if every token is a digit word (or 'double X'/'triple X')."""
    i = 0
    while i < len(tokens):
        t = tokens[i].lower()
        if t in ("double", "triple"):
            if i + 1 < len(tokens) and tokens[i + 1].lower() in _DIGIT_WORDS:
                i += 2
                continue
            return False
        if t in _DIGIT_WORDS:
            i += 1
            continue
        return False
    return True


def _parse_clock_time(body):
    """Parse 'nine thirty' / 'three fifteen' / 'two' as clock time -> '9:30' / '3:15' / '2'."""
    parts = body.split()
    lp = len(parts)
    if lp == 1:
        h = _NUMBER_ONES.get(parts[0])
        if h is not None and 1 <= h <= 12:
            return str(h)
    elif lp == 2:
        h = _NUMBER_ONES.get(parts[0])
        m1 = _NUMBER_ONES.get(parts[1])  # teens: 10-19
        m2 = _NUMBER_TENS.get(parts[1])  # tens: 20-59
        if h is not None and 1 <= h <= 12:
            if m1 is not None and 10 <= m1 < 20:
                return f"{h}:{m1}"
            if m2 is not None and m2 >= 20:
                return f"{h}:{m2}"
    elif lp == 3:
        h = _NUMBER_ONES.get(parts[0])
        m2 = _NUMBER_TENS.get(parts[1])
        o = _NUMBER_ONES.get(parts[2])
        if h is not None and 1 <= h <= 12 and m2 is not None and o is not None:
            return f"{h}:{m2 + o:02d}"
    return None


def _normalize_a_an(phrase):
    """Replace a leading 'a'/'an' before a number word with 'one' (e.g. 'a hundred' -> 'one hundred')."""
    parts = phrase.split()
    if parts and parts[0] in ("a", "an") and len(parts) > 1:
        parts[0] = "one"
    return " ".join(parts)


_TIME_MARKERS = (("o'clock", "o'clock"), ("p.m.", "PM"), ("a.m.", "AM"), ("p m", "PM"), ("a m", "AM"), ("pm", "PM"), ("am", "AM"))
_TIME_MARKERS_TUPLE = ("o'clock", "p.m.", "a.m.", "p m", "a m", "pm", "am")

def _convert_number_phrase(m):
    """Try to convert a number-word phrase to digits; return unchanged if unparseable."""
    phrase = m.group(0).strip()
    tokens = phrase.replace("-", " ").split()
    lower_tokens = [t.lower() for t in tokens]
    joined = " ".join(lower_tokens)

    # Protect ambiguous standalone "one" ("the one", "no one", "one of", "one thing"...)
    if len(lower_tokens) == 1 and lower_tokens[0] == "one":
        before = m.string[:m.start()].rstrip().rsplit(None, 1)[-1].lower() if m.start() > 0 else ""
        after = m.string[m.end():].lstrip().split(None, 1)[0].lower() if m.end() < len(m.string) else ""
        if before in _ONE_BEFORE_PROTECT or after in _ONE_AFTER_PROTECT:
            return m.group(0)
        return "1"

    # Digit strings / phone numbers (2+ consecutive digit words): "six seven zero" -> "670"
    if len(lower_tokens) >= 2 and _is_pure_digit_string(lower_tokens):
        return _expand_digit_string(m)

    # Percent: "fifty percent" -> "50%"
    if joined.endswith("percent"):
        body = joined[: -len("percent")].strip()
        if body:
            n = _parse_cardinal(_normalize_a_an(body).split())
            if n is not None:
                return f"{n}%"

    # Time: "five pm" / "five p m" / "five p.m." / "five o'clock" -> "5 PM" / "5 o'clock"
    if joined.endswith(_TIME_MARKERS_TUPLE):
        for marker, repl in _TIME_MARKERS:
            if joined.endswith(marker):
                body = joined[: -len(marker)].strip()
                if body:
                    n = _parse_cardinal(_normalize_a_an(body).split())
                    if n is not None:
                        return f"{n} {repl}"
                    clock = _parse_clock_time(_normalize_a_an(body))
                    if clock is not None:
                        return f"{clock} {repl}"
                break

    # Decimal: "three point one four" -> "3.14"
    if " point " in joined:
        left, _, right = joined.partition(" point ")
        n = _parse_cardinal(_normalize_a_an(left).split())
        if n is not None and right:
            frac = []
            ok = True
            for w in right.split():
                if w in _DIGIT_WORDS:
                    frac.append(_DIGIT_WORDS[w])
                elif w in _NUMBER_TENS or w in _NUMBER_ONES:
                    frac.append(str(_parse_cardinal([w])))
                else:
                    ok = False
                    break
            if ok and frac:
                return f"{n}.{''.join(frac)}"

    # Years
    if joined.startswith(("nineteen ", "twenty ", "two thousand")):
        y = _parse_year(joined)
        if y is not None:
            return str(y)

    # Ordinals
    ord_ = _parse_ordinal(tokens)
    if ord_ is not None:
        n, suffix = ord_
        return f"{n}{suffix}"

    # Cardinals
    if lower_tokens[0] in ("a", "an") and len(lower_tokens) > 1:
        card_tokens = ["one"] + lower_tokens[1:]
    else:
        card_tokens = lower_tokens

    n = _parse_cardinal(card_tokens)
    if n is not None:
        return f"{n:,}" if n >= 1000 else str(n)

    return m.group(0)


def _has_number_words(text_lower: str) -> bool:
    for word in text_lower.split():
        w = word.strip(".,!?:;\"'()[]{}")
        if w in _ALL_NUMBER_WORDS_SET:
            return True
        if "-" in w:
            for part in w.split("-"):
                if part in _ALL_NUMBER_WORDS_SET:
                    return True
    return False


def convert_number_words_to_digits(text: str, text_lower: str | None = None) -> str:
    """Convert spoken number words in text to digits (no-op if disabled via env or runtime toggle)."""
    if os.environ.get("VT_NUMBER_DIGITS", "1") != "1" or not _number_digits_enabled:
        return text
    if not text:
        return ""
    if text_lower is None:
        text_lower = text.lower()
    if not _has_number_words(text_lower):
        return text
    converted = _NUMBER_PHRASE_REGEX.sub(_convert_number_phrase, text)
    if _DIGIT_STRING_REGEX.search(converted):
        converted = _DIGIT_STRING_REGEX.sub(_expand_digit_string, converted)
    return converted


_number_digits_enabled = True


def set_number_digits_enabled(enabled: bool) -> None:
    """Runtime toggle for number-word -> digit conversion (used by the 'n' hotkey)."""
    global _number_digits_enabled
    _number_digits_enabled = bool(enabled)


# ---------------------------------------------------------------------------
# Punctuation & Formatting Modes
# ---------------------------------------------------------------------------
# 1. "full": standard casing & terminal punctuation (default)
# 2. "no_terminal_period" (semi-formal): internal punctuation kept, no trailing period
# 3. "no_punctuation": all punctuation stripped, casing preserved
# 4. "lowercase_no_punctuation": all punctuation stripped and lowercased
_PUNCTUATION_MODE = "full"


def set_punctuation_mode(mode: str) -> None:
    """Set global punctuation mode."""
    global _PUNCTUATION_MODE
    if mode:
        _PUNCTUATION_MODE = str(mode).strip().lower()


def get_punctuation_mode() -> str:
    """Get global punctuation mode."""
    return _PUNCTUATION_MODE


def _strip_punctuation(text: str) -> str:
    """Remove punctuation while preserving intra-token separators.

    Decimals (``3.14``), IPs (``192.168.1.10``), paths (``/etc/nixos``) and
    contractions (``don't``) keep their separators; everything else is dropped.
    """
    def _keep_or_drop(m: re.Match) -> str:
        ch = m.group(0)
        if ch == "-":
            return ch
        start, end = m.start(), m.end()
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before.isalnum() and after.isalnum():
            return ch
        if ch in "/." and not before.isalnum() and after.isalnum():
            return ch
        return ""

    return " ".join(re.sub(r"[^\w\s-]", _keep_or_drop, text).split())


def apply_punctuation_mode(text: str, mode: str | None = None) -> str:
    """Format transcribed text according to the selected punctuation mode.

    Modes:
      - full (default): standard capitalization and terminal/internal punctuation.
      - no_terminal_period (semi-formal): retains internal punctuation and capitalization,
        but omits trailing periods at the end of the text.
      - no_punctuation: removes punctuation marks, preserving casing and intra-token
        separators (decimals, IPs, paths, contractions).
      - lowercase_no_punctuation: as above, and lowercased.
    """
    if not text:
        return text
    mode_str = (mode or _PUNCTUATION_MODE or "full").strip().lower().replace("-", "_")

    if mode_str in ("no_terminal_period", "semi_formal", "no_period", "no_ending_period"):
        trimmed = text.rstrip()
        if trimmed.endswith("."):
            return trimmed.rstrip(". ")
        return trimmed

    elif mode_str in ("no_punctuation", "none", "no_punct"):
        return _strip_punctuation(text)

    elif mode_str in ("lowercase_no_punctuation", "lowercase_no_punct"):
        return _strip_punctuation(text.lower())

    return text


# ---------------------------------------------------------------------------
# High-Speed Trie-Compacted Custom Word & Phrase Dictionary Replacer
# ---------------------------------------------------------------------------
# Replaces custom words and multi-word phrases (e.g. "pull request" -> "PR",
# "v l l m" -> "vLLM", "github" -> "GitHub") in ~0.005 ms using a single-pass
# C-level Trie-compacted regex with word boundaries.



_CUSTOM_DICTIONARY: dict[str, str] = {}
_CUSTOM_DICT_REPLACER = None
_CUSTOM_DICT_INITIALIZED = False
HOMOPHONE_REPAIR_PATTERNS: list[tuple[re.Pattern, str]] = []


class ContextualRule:
    """A context-aware word replacement rule that checks surrounding triggers and guards."""

    def __init__(
        self,
        target: str,
        spoken: list[str],
        triggers_before: list[str] | None = None,
        triggers_after: list[str] | None = None,
        guards: list[str] | None = None,
    ):
        self.target = str(target).strip()
        self.spoken = [" ".join(str(s).strip().split()).lower() for s in (spoken or []) if str(s).strip()]
        self.triggers_before = [" ".join(str(b).strip().split()).lower() for b in (triggers_before or []) if str(b).strip()]
        self.triggers_after = [" ".join(str(a).strip().split()).lower() for a in (triggers_after or []) if str(a).strip()]
        self.guards = [" ".join(str(g).strip().split()).lower() for g in (guards or []) if str(g).strip()]

        # Compile guard regex (negative lookahead/lookbehind filter for standard English)
        self.guard_pattern = None
        if self.guards:
            guards_escaped = "|".join(re.escape(g) for g in sorted(self.guards, key=len, reverse=True))
            self.guard_pattern = re.compile(rf"(?<!\w)({guards_escaped})(?!\w)", re.IGNORECASE)

        # Compile trigger patterns
        self.patterns: list[tuple[str, re.Pattern]] = []
        if self.triggers_before and self.spoken:
            bef_esc = "|".join(re.escape(b) for b in sorted(self.triggers_before, key=len, reverse=True))
            spk_esc = "|".join(re.escape(s) for s in sorted(self.spoken, key=len, reverse=True))
            # Matches: <triggers_before> (optional filler like "it", "code", "changes") <spoken>
            p = re.compile(
                rf"(?<!\w)({bef_esc})\s+(?:(?:it|changes|code|branch)\s+)?({spk_esc})(?!\w)",
                re.IGNORECASE,
            )
            self.patterns.append(("before", p))

        if self.triggers_after and self.spoken:
            aft_esc = "|".join(re.escape(a) for a in sorted(self.triggers_after, key=len, reverse=True))
            spk_esc = "|".join(re.escape(s) for s in sorted(self.spoken, key=len, reverse=True))
            p = re.compile(rf"(?<!\w)({spk_esc})\s+({aft_esc})(?!\w)", re.IGNORECASE)
            self.patterns.append(("after", p))

    def apply(self, text: str) -> str:
        text_lower = text.lower()
        if not any(s in text_lower for s in self.spoken):
            return text

        if self.guard_pattern and self.guard_pattern.search(text):
            return text

        for kind, pat in self.patterns:
            if kind == "before":
                text = pat.sub(rf"\1 {self.target}", text)
            elif kind == "after":
                text = pat.sub(rf"{self.target} \2", text)
        return text

    def to_dict(self) -> dict:
        d = {"target": self.target, "spoken": self.spoken}
        if self.triggers_before:
            d["triggers_before"] = self.triggers_before
        if self.triggers_after:
            d["triggers_after"] = self.triggers_after
        if self.guards:
            d["guards"] = self.guards
        return d


_CONTEXTUAL_RULES: list[ContextualRule] = []


def set_contextual_rules(rules: list[dict | ContextualRule] | None) -> None:
    """Set and compile contextual word/phrase rules."""
    global _CONTEXTUAL_RULES
    if not rules:
        _CONTEXTUAL_RULES = []
        return

    compiled = []
    for r in rules:
        if isinstance(r, ContextualRule):
            compiled.append(r)
        elif isinstance(r, dict):
            target = r.get("target")
            spoken = r.get("spoken")
            if target and spoken:
                compiled.append(
                    ContextualRule(
                        target=target,
                        spoken=spoken if isinstance(spoken, list) else [spoken],
                        triggers_before=r.get("triggers_before") or r.get("before"),
                        triggers_after=r.get("triggers_after") or r.get("after"),
                        guards=r.get("guards") or r.get("protect"),
                    )
                )
    _CONTEXTUAL_RULES = compiled


def get_contextual_rules() -> list[ContextualRule]:
    """Returns active contextual rules."""
    return list(_CONTEXTUAL_RULES)

def set_custom_dictionary(mapping: dict[str, str] | None) -> None:
    """
    Sets and compiles the custom word/phrase replacement dictionary.
    Compiles phrases into a ~0.005 ms Trie-compacted regex pattern.
    """
    global _CUSTOM_DICTIONARY, _CUSTOM_DICT_REPLACER, _CUSTOM_DICT_INITIALIZED
    _CUSTOM_DICT_INITIALIZED = True
    if not mapping:
        _CUSTOM_DICTIONARY = {}
        _CUSTOM_DICT_REPLACER = None
        return

    # Clean and normalize keys (single-space whitespace, lowercase, stripped)
    clean_map = {}
    for k, v in mapping.items():
        if k is not None and v is not None:
            clean_k = " ".join(str(k).strip().split()).lower()
            if clean_k:
                clean_map[clean_k] = str(v)

    _CUSTOM_DICTIONARY = clean_map
    if not _CUSTOM_DICTIONARY:
        _CUSTOM_DICT_REPLACER = None
        return

    # Automatically register capitalized/mixed-case target words to TECHNICAL_ACRONYMS_AND_PROPER_NOUNS
    # so mid-sentence decapitalization won't lower them
    for target in _CUSTOM_DICTIONARY.values():
        for word in target.split():
            clean_w = word.strip(".,!?:;\"'()[]{}")
            if any(c.isupper() for c in clean_w):
                TECHNICAL_ACRONYMS_AND_PROPER_NOUNS.add(clean_w)

    sorted_phrases = sorted(_CUSTOM_DICTIONARY.keys(), key=len, reverse=True)
    trie_body = _build_trie_regex(sorted_phrases)
    pattern = re.compile(rf"(?<!\w)({trie_body})(?!\w)", re.IGNORECASE)

    def _replace(m: re.Match) -> str:
        key = m.group(0).lower()
        return _CUSTOM_DICTIONARY.get(key, m.group(0))

    _CUSTOM_DICT_REPLACER = lambda text: pattern.sub(_replace, text)


def get_custom_dictionary() -> dict[str, str]:
    """Returns a copy of the active custom replacement dictionary."""
    return dict(_CUSTOM_DICTIONARY)


def reset_custom_dictionary() -> None:
    """Resets the custom replacement dictionary and contextual rules to empty."""
    set_custom_dictionary(None)
    set_contextual_rules(None)


def load_custom_dictionary_from_file(file_path: str | os.PathLike) -> dict[str, str]:
    """Loads dictionary mappings from a YAML or JSON file and applies them."""
    p = Path(file_path)
    if not p.exists():
        return {}
    try:
        content = p.read_text(encoding="utf-8")
        if p.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(content) or {}
            except Exception:
                data = json.loads(content) if content.strip() else {}
        else:
            data = json.loads(content) if content.strip() else {}
        
        if isinstance(data, dict):
            if "contextual_rules" in data and isinstance(data["contextual_rules"], list):
                set_contextual_rules(data["contextual_rules"])
            if "dictionary" in data and isinstance(data["dictionary"], dict):
                mapping = data["dictionary"]
            else:
                mapping = {k: v for k, v in data.items() if k != "contextual_rules" and isinstance(v, (str, int, float))}
        else:
            mapping = {}
            
        set_custom_dictionary(mapping)
        return mapping
    except Exception as e:
        print(f"⚠️ [post_processor] Error loading dictionary from {file_path}: {e}")
        return {}


def _auto_load_dictionary_if_needed() -> None:
    """Auto-detects and loads dictionary from config.yaml or dictionary.yaml on first use."""
    global _CUSTOM_DICT_INITIALIZED
    if _CUSTOM_DICT_INITIALIZED:
        return
    _CUSTOM_DICT_INITIALIZED = True
    
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        Path("config/dictionary.yaml"),
        Path("config/dictionary.yml"),
        Path("config/dictionary.json"),
        Path("dictionary.yaml"),
        Path("dictionary.json"),
        Path("config/config.yaml"),
        Path("config.yaml"),
        repo_root / "config/dictionary.yaml",
        repo_root / "config/dictionary.yml",
        repo_root / "config/dictionary.json",
        repo_root / "config/config.yaml",
    ]
    for c in candidates:
        if c.exists():
            try:
                content = c.read_text(encoding="utf-8")
                if c.suffix in (".yaml", ".yml"):
                    try:
                        import yaml
                        data = yaml.safe_load(content) or {}
                    except Exception:
                        data = json.loads(content) if content.strip() else {}
                else:
                    data = json.loads(content) if content.strip() else {}
                
                if isinstance(data, dict):
                    if "contextual_rules" in data and isinstance(data["contextual_rules"], list):
                        set_contextual_rules(data["contextual_rules"])
                    dict_file = data.get("dictionary_file")
                    if dict_file and Path(dict_file).exists():
                        load_custom_dictionary_from_file(dict_file)
                        return
                    if "dictionary" in data and isinstance(data["dictionary"], dict):
                        set_custom_dictionary(data["dictionary"])
                        return
                    if "dictionary" in c.name and data:
                        set_custom_dictionary(data)
                        return
            except Exception:
                pass


# Initialize custom dictionary once at module load
_auto_load_dictionary_if_needed()


COMMA_DUP_REGEX = re.compile(r"[,]{2,}")
PUNCT_COMMA_REGEX = re.compile(r"([.?!,])\s*,\s*")
COMMA_NO_SPACE_REGEX = re.compile(r",([a-zA-Z])")
SPACE_BEFORE_COMMA_REGEX = re.compile(r"\s+,")
MULTI_SPACE_REGEX = re.compile(r"\s{2,}")
STANDALONE_I_REGEX = re.compile(r"\bi\b")
CONTRACTION_I_REGEX = re.compile(r"\bi('[a-z]+)\b")

_HALLUCINATION_KEYWORDS = ("watching", "subtitles", "subscribe")
_MUTTERING_KEYWORDS = ("oop", "whoop", "never")
_FILLER_KEYWORDS = ("um", "uh", "er", "ah")


def clean_speech_transcription(
    text: str,
    skip_slm: bool = False,
    punctuation_mode: str | None = None,
    is_intermediate: bool = False
) -> str:
    """
    Cleans raw speech transcription text of ASR artifacts, false sentence breaks,
    repeated stutters, verbal self-corrections, and trailing hallucinations.
    """
    if not text:
        return ""
        
    cleaned = text
    cleaned_lower = text.lower()
    
    # 0. Apply verbal edit self-correction pre-pass & hesitation filler removal
    new_cleaned = process_verbal_retractions(cleaned, cleaned_lower)
    if new_cleaned is not cleaned:
        cleaned = new_cleaned
        cleaned_lower = cleaned.lower()
        
    if ("um" in cleaned_lower or "uh" in cleaned_lower or "ah" in cleaned_lower or
        " er" in cleaned_lower or "er " in cleaned_lower or cleaned_lower.startswith("er") or cleaned_lower.endswith("er")):
        if FILLER_WORDS_REGEX.search(cleaned):
            cleaned = FILLER_WORDS_REGEX.sub(" ", cleaned)
            cleaned_lower = cleaned.lower()

    # 0b. Recover the "AI" acronym from common ASR mis-hearings ("a eyes" -> "AI")
    if "eyes" in cleaned_lower:
        cleaned = AI_MISHEARINGS_REGEX.sub("AI", cleaned)
        cleaned_lower = cleaned.lower()

    # 0c. Scrub stray microphone click / onset consonant clipping before words ("t needs" -> "needs")
    if cleaned_lower.startswith("t ") or cleaned_lower.startswith("'t "):
        cleaned = LEADING_STRAY_T_REGEX.sub(r"\1", cleaned)
        cleaned_lower = cleaned.lower()

    # 0d. Recover phonetic mis-hearings of "addressing" ("needs a. dressing" / "needs a dressing" -> "needs addressing")
    if "dressing" in cleaned_lower:
        cleaned = ADDRESSING_MISHEARINGS_REGEX.sub(r"\1 addressing", cleaned)
        cleaned_lower = cleaned.lower()
    
    # 0. Apply optional vLLM / SLM rewrite pass (unless bypassed for intermediate streaming micro-chunks)
    if not skip_slm and os.environ.get("VT_ENABLE_SLM", "0") == "1":
        cleaned = process_slm_llm_rewrite(cleaned)
        cleaned_lower = cleaned.lower()
    
    # 1. Hallucination and trailing muttering stripping
    if ("watching" in cleaned_lower or "subtitles" in cleaned_lower or "subscribe" in cleaned_lower or "==" in cleaned_lower):
        for pat in HALLUCINATION_PATTERNS:
            cleaned = pat.sub("", cleaned)
        cleaned_lower = cleaned.lower()
        
    if ("oop" in cleaned_lower or "whoop" in cleaned_lower or "never" in cleaned_lower):
        if cleaned_lower.rstrip(" .?!,;:").endswith(("oops", "whoops", "oopsy", "whoopsy", "oop", "opps", "nevermind", "never mind")):
            while True:
                new_cleaned = TRAILING_MUTTERINGS_REGEX.sub(r"\1", cleaned)
                if new_cleaned == cleaned:
                    break
                cleaned = new_cleaned
            cleaned = STANDALONE_MUTTERINGS_REGEX.sub("", cleaned)
            cleaned_lower = cleaned.lower()

    if len(cleaned) <= 40:
        stripped_h = cleaned.strip(" .?!").lower()
        if stripped_h in ("you", "bye", "thank you", "thanks", "subtitles", "shh", "shhh", "ptl"):
            return ""
    
    # If the text was reduced to only punctuation / whitespace, return empty
    if not cleaned.strip(".,!?;: \t\n\r"):
        return ""
    
    # 2 & 3. Deduplicate repeated words across punctuation & direct filler stutters
    has_punct_cand, has_direct = _check_stutters(cleaned_lower)
    if has_punct_cand and any(p in cleaned for p in ".-"):
        cleaned = STUTTER_PUNCT_REGEX.sub(_collapse_stutter_punct, cleaned)
    if has_direct:
        cleaned = STUTTER_DIRECT_REGEX.sub(r"\1", cleaned)
    if "to" in cleaned_lower and ("into" in cleaned_lower or "onto" in cleaned_lower or "in to" in cleaned_lower or "on to" in cleaned_lower):
        cleaned = PREPOSITION_COMPOUND_STUTTER_REGEX.sub(lambda m: "into" if "into" in m.group(0).lower() else "onto", cleaned)
    
    # 4-9. Sentence boundary & clause linking fixes
    if MID_SENTENCE_BOUNDARY_REGEX.search(cleaned):
        has_period = "." in cleaned

        # 4. Fix isolated single-word discourse markers (e.g. "So. I am" -> "So, I am", "Yeah. Revert" -> "Yeah, revert")
        if has_period and DISCOURSE_STARTERS_REGEX.search(cleaned):
            def _fix_discourse(m):
                prefix = m.group(1)
                word = m.group(2)
                next_char = _preserve_i_casing(m.group(3), m.string, m.end())
                return f"{prefix}{word}, {next_char}"
            cleaned = DISCOURSE_STARTERS_REGEX.sub(_fix_discourse, cleaned)
        
        # 5. Dangling prepositions & determiners before period (e.g. "put a. period" -> "put a period")
        if DANGLING_WORDS_REGEX.search(cleaned):
            def _fix_dangling(m):
                w1 = m.group(1)
                next_char = _preserve_i_casing(m.group(2), m.string, m.end())
                return f"{w1} {next_char}"
            cleaned = DANGLING_WORDS_REGEX.sub(_fix_dangling, cleaned)
        
        if has_period:
            # 6. Incomplete linking verbs followed by lowercase continuation (e.g. "thing was. something")
            if LINKING_VERB_LOWER_REGEX.search(cleaned):
                cleaned = LINKING_VERB_LOWER_REGEX.sub(r"\1 \2", cleaned)
            
            # 7. Coordinating conjunctions after period (e.g. "commit. and force push" -> "commit, and force push")
            if ("and" in cleaned_lower or "or" in cleaned_lower or "but" in cleaned_lower or
                "so" in cleaned_lower or "yet" in cleaned_lower or "nor" in cleaned_lower):
                cleaned = COORD_CONJUNCTIONS_REGEX.sub(lambda m: f", {m.group(1).lower()}", cleaned)
            
            # 8. Subordinating conjunctions after period (e.g. ". because", ". which")
            if ("because" in cleaned_lower or "which" in cleaned_lower or "that" in cleaned_lower or
                "though" in cleaned_lower or "although" in cleaned_lower):
                cleaned = SUBORD_CONJUNCTIONS_REGEX.sub(lambda m: f" {m.group(1).lower()}", cleaned)
            
            # 9. General lowercase continuation after period (e.g. ". something" -> " something")
            if LOWERCASE_AFTER_PERIOD_REGEX.search(cleaned):
                cleaned = LOWERCASE_AFTER_PERIOD_REGEX.sub(r" \1", cleaned)
    
    # 10. Clean up duplicate punctuation and normalize spacing
    if ",," in cleaned:
        cleaned = COMMA_DUP_REGEX.sub(",", cleaned)
    if "," in cleaned:
        cleaned = PUNCT_COMMA_REGEX.sub(r"\1 ", cleaned)
        if " ," in cleaned:
            cleaned = SPACE_BEFORE_COMMA_REGEX.sub(",", cleaned)
        cleaned = COMMA_NO_SPACE_REGEX.sub(r", \1", cleaned)
    if "  " in cleaned:
        cleaned = MULTI_SPACE_REGEX.sub(" ", cleaned)

    # 10b. Convert spoken number words to digits ("twenty five" -> "25", phone digit strings, ...)
    cleaned = convert_number_words_to_digits(cleaned, cleaned_lower)

    # 11. Normalize standalone I and contractions
    if "i" in cleaned:
        if " i " in cleaned or cleaned.startswith("i ") or cleaned.endswith(" i") or STANDALONE_I_REGEX.search(cleaned):
            cleaned = STANDALONE_I_REGEX.sub("I", cleaned)
        if "i'" in cleaned:
            cleaned = CONTRACTION_I_REGEX.sub(r"I\1", cleaned)
    
    # 12. Normalize mid-sentence random capitalizations
    cleaned = normalize_mid_sentence_casing(cleaned)
    
    # 13. Scrub mid-phrase spurious question marks
    if "?" in cleaned:
        cleaned = MID_PHRASE_QUESTION_MARK_REGEX.sub(r"\1 \2", cleaned)
    
    # 13c. Apply legacy homophone repair patterns if configured
    if HOMOPHONE_REPAIR_PATTERNS:
        for pat, repl in HOMOPHONE_REPAIR_PATTERNS:
            cleaned = pat.sub(repl, cleaned)

    # 13d. Apply context-aware word/phrase disambiguation rules
    if _CONTEXTUAL_RULES:
        for rule in _CONTEXTUAL_RULES:
            cleaned = rule.apply(cleaned)

    # 13e. Apply custom vocabulary / phrase dictionary substitutions (~5 us)
    if _CUSTOM_DICT_REPLACER is not None:
        cleaned = _CUSTOM_DICT_REPLACER(cleaned)

    # 13f. Clean spoken Unix file paths and subnet notation
    if "slash" in cleaned_lower:
        cleaned = clean_spoken_paths(cleaned)

    cleaned = cleaned.strip()
    if not cleaned or not cleaned.strip(".,!?;: \t\n\r"):
        return ""
        
    # 14. Ensure complete statement utterances end with terminal punctuation
    if not is_intermediate:
        if not cleaned.endswith((".", "!", "?", ":")):
            if len(cleaned.split(None, 3)) >= 3:
                cleaned += "."

    # 15. Apply punctuation mode formatting (if not intermediate chunk)
    if not is_intermediate:
        cleaned = apply_punctuation_mode(cleaned, mode=punctuation_mode)
        
    return cleaned

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

import re

# Precompiled hallucination patterns
HALLUCINATION_PATTERNS = [
    re.compile(r"\bthanks?\s+(for\s+)?watching[.!?,]*\b", re.IGNORECASE),
    re.compile(r"\bthank\s+you\s+for\s+watching[.!?,]*\b", re.IGNORECASE),
    re.compile(r"\bsubtitles\s+by\s+.*$", re.IGNORECASE),
    re.compile(r"\bplease\s+subscribe[.!?,]*\b", re.IGNORECASE),
]

# Words that can never grammatically end an English sentence / clause
DANGLING_WORDS_REGEX = re.compile(
    r"\b("
    r"a|an|the|my|your|his|her|our|their|its|"
    r"of|to|in|for|with|on|at|from|by|about|into|through|during|below|between|under|without|"
    r"and|or|but|because|if|while|since|until|unless|"
    r"very|too|quite|really|such"
    r")\s*[.?!]\s+([a-zA-Z])",
    re.IGNORECASE
)

# Common conversational discourse starters
DISCOURSE_STARTERS_REGEX = re.compile(
    r"(^|(?<=[.?!]\s))(yeah|yes|no|so|well|okay|ok|sure|right|actually|meanwhile|anyway|anyways)\s*[.]\s+([a-zA-Z])",
    re.IGNORECASE
)

# Repeated words across punctuation (e.g. "about. about", "might. Might", "a. a")
STUTTER_PUNCT_REGEX = re.compile(
    r"\b([a-zA-Z]+)\s*[.,?!;:]+\s+(?i:\1)\b"
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

# Coordinating conjunctions following a period
COORD_CONJUNCTIONS_REGEX = re.compile(
    r"[.]\s+(and|or|but|so|yet|nor)\b",
    re.IGNORECASE
)

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
    re.compile(r"(?:[,.]?\s*)(?:scratch\s+that|strike\s+that|never\s+mind\s+that)[.!?,;:]*\s*$", re.IGNORECASE),
    # "X... actually Y" / "X... make that Y" / "X... no wait Y" / "X... I mean Y" / "X... or rather Y"
    re.compile(r"\b([a-zA-Z0-9$%\.:]+(?:\s+[a-zA-Z0-9$%\.:]+){0,2})\s*[,.]?\s*(?:actually|make\s+that|no\s+wait|I\s+mean|or\s+rather)\s+([a-zA-Z0-9$%\.:]+(?:\s+[a-zA-Z0-9$%\.:]+){0,2})\b", re.IGNORECASE),
]

def process_verbal_retractions(text: str) -> str:
    """
    Applies zero-latency verbal self-correction parsing:
    Replaces retracted phrases ('5 PM... actually 6 PM' -> '6 PM')
    and handles voice deletions ('scratch that').
    """
    if not text:
        return ""
        
    cleaned = text
    # 1. Handle "scratch that" tail deletion
    cleaned = RETRACTION_REPLACEMENT_PATTERNS[0].sub("", cleaned)
    
    # 2. Handle verbal replacements ("X... actually Y", "X... I mean Y")
    def _replace_retraction(m):
        return m.group(2)
        
    cleaned = RETRACTION_REPLACEMENT_PATTERNS[1].sub(_replace_retraction, cleaned)
    return cleaned.strip()

import json
import urllib.request
import urllib.error

VLLM_API_URL = os.environ.get("VT_VLLM_URL", "http://localhost:8000/v1/chat/completions")

def process_slm_llm_rewrite(text: str, timeout_sec: float = 0.3) -> str:
    """
    Passes speech transcript through local vLLM / SLM (Qwen2.5-0.5B / Llama-3.2-1B)
    to perform real-time speech self-correction and grammar polishing.
    """
    if not text or len(text.strip()) < 5 or os.environ.get("VT_ENABLE_SLM", "0") != "1":
        return text
        
    payload = {
        "model": os.environ.get("VT_SLM_MODEL", "Qwen/Qwen2.5-0.5B-Instruct"),
        "messages": [
            {
                "role": "system",
                "content": "You are a real-time speech dictation cleaner. Remove filler words (um, uh) and execute verbal corrections (e.g. '5 PM... actually 6 PM' -> '6 PM'). Output ONLY the final clean text without commentary."
            },
            {
                "role": "user",
                "content": text
            }
        ],
        "temperature": 0.0,
        "max_tokens": 150
    }
    
    try:
        req = urllib.request.Request(
            VLLM_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            clean_output = res_data['choices'][0]['message']['content'].strip()
            if clean_output:
                return clean_output
    except Exception:
        # Fallback cleanly if vLLM service is not active
        pass
        
    return text

def clean_speech_transcription(text: str) -> str:
    """
    Cleans raw speech transcription text of ASR artifacts, false sentence breaks,
    repeated stutters, verbal self-corrections, and trailing hallucinations.
    """
    if not text:
        return ""
        
    cleaned = text
    
    # 0. Apply verbal edit self-correction pre-pass
    cleaned = process_verbal_retractions(cleaned)
    
    # 0. Apply optional vLLM / SLM rewrite pass
    cleaned = process_slm_llm_rewrite(cleaned)
    
    # 1. Hallucination and trailing muttering stripping
    for pat in HALLUCINATION_PATTERNS:
        cleaned = pat.sub("", cleaned)
        
    while True:
        new_cleaned = TRAILING_MUTTERINGS_REGEX.sub(r"\1", cleaned)
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned
        
    cleaned = STANDALONE_MUTTERINGS_REGEX.sub("", cleaned)
    
    # If the text was reduced to only punctuation / whitespace, return empty
    if re.match(r"^[.,!?;:\s]*$", cleaned):
        return ""
    
    # 2. Deduplicate repeated words across punctuation (e.g. "about. about" -> "about", "a. a" -> "a")
    cleaned = STUTTER_PUNCT_REGEX.sub(r"\1", cleaned)
    
    # 3. Deduplicate direct filler stutters (e.g. "about about" -> "about", "the the" -> "the")
    cleaned = STUTTER_DIRECT_REGEX.sub(r"\1", cleaned)
    
    # 4. Fix isolated single-word discourse markers (e.g. "So. I am" -> "So, I am", "Yeah. Revert" -> "Yeah, revert")
    def _fix_discourse(m):
        prefix = m.group(1)
        word = m.group(2)
        next_char = _preserve_i_casing(m.group(3), m.string, m.end())
        return f"{prefix}{word}, {next_char}"
    cleaned = DISCOURSE_STARTERS_REGEX.sub(_fix_discourse, cleaned)
    
    # 5. Dangling prepositions & determiners before period (e.g. "put a. period" -> "put a period")
    def _fix_dangling(m):
        w1 = m.group(1)
        next_char = _preserve_i_casing(m.group(2), m.string, m.end())
        return f"{w1} {next_char}"
    cleaned = DANGLING_WORDS_REGEX.sub(_fix_dangling, cleaned)
    
    # 6. Incomplete linking verbs followed by lowercase continuation (e.g. "thing was. something")
    cleaned = LINKING_VERB_LOWER_REGEX.sub(r"\1 \2", cleaned)
    
    # 7. Coordinating conjunctions after period (e.g. "commit. and force push" -> "commit, and force push")
    cleaned = COORD_CONJUNCTIONS_REGEX.sub(lambda m: f", {m.group(1).lower()}", cleaned)
    
    # 8. Subordinating conjunctions after period (e.g. ". because", ". which")
    cleaned = SUBORD_CONJUNCTIONS_REGEX.sub(lambda m: f" {m.group(1).lower()}", cleaned)
    
    # 9. General lowercase continuation after period (e.g. ". something" -> " something")
    cleaned = LOWERCASE_AFTER_PERIOD_REGEX.sub(r" \1", cleaned)
    
    # 10. Clean up duplicate punctuation and normalize spacing
    cleaned = re.sub(r"[,]{2,}", ",", cleaned)
    cleaned = re.sub(r"([.?!,])\s*,\s*", r"\1 ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    
    # 11. Normalize standalone I and contractions
    cleaned = re.sub(r"\bi\b", "I", cleaned)
    cleaned = re.sub(r"\bi('[a-z]+)\b", r"I\1", cleaned)
    
    cleaned = cleaned.strip()
    if re.match(r"^[.,!?;:\s]*$", cleaned):
        return ""
        
    return cleaned

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

# Trailing muttered self-corrections / speech sign-offs at end of dictation
TRAILING_MUTTERINGS_REGEX = re.compile(
    r"([.?!,;:]|\s)\s*(?:oops|whoops|oopsy|whoopsy|oop|opps|nevermind|never\s+mind|you|bye)[.!?,;:]*\s*$",
    re.IGNORECASE
)

STANDALONE_MUTTERINGS_REGEX = re.compile(
    r"^\s*(?:oops|whoops|oopsy|whoopsy|oop|opps|nevermind|never\s+mind|you|bye)[.!?,;:]*\s*$",
    re.IGNORECASE
)

def _preserve_i_casing(next_char, full_string, end_pos):
    """Ensure standalone I remains capitalized."""
    if next_char.lower() == "i" and (len(full_string) <= end_pos or not full_string[end_pos].isalpha()):
        return "I"
    return next_char.lower()

def clean_speech_transcription(text: str) -> str:
    """
    Cleans raw speech transcription text of ASR artifacts, false sentence breaks,
    repeated stutters, trailing mutterings (Oops, Whoops), and trailing hallucinations.
    """
    if not text:
        return ""
        
    cleaned = text
    
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

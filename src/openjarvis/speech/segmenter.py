"""Intelligent sentence segmenter for low-latency, natural TTS streaming.

Splits text into prosodically complete, natural sentence units while protecting
abbreviations, decimal numbers, version identifiers, and honorifics from premature
splitting. Ensures smooth, melodic delivery without unnatural sentence fragments.
"""

from __future__ import annotations

import re
from typing import Iterator, List

# Common English abbreviations that should not trigger sentence boundaries
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "rev", "gen", "col", "capt", "sgt",
    "vs", "v", "etc", "eg", "e.g", "ie", "i.e", "approx", "dept", "est", "govt",
    "inc", "corp", "ltd", "co", "fig", "vol", "no", "nos", "p", "pp", "sec",
    "min", "hr", "hrs", "sq", "ft", "in", "yd", "mi", "km", "cm", "mm",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
})

# Sentence-ending punctuation followed by whitespace and a capital letter or end of string
_SPLIT_REGEX = re.compile(r"([.!?]+[\s\r\n]+)")


def is_abbreviation(token: str) -> bool:
    """Check if token ends in a dot and matches a recognized abbreviation."""
    cleaned = token.lower().strip(".,;:!?()[]{}\"'")
    return cleaned in ABBREVIATIONS


def segment_sentences(text: str, min_chars: int = 15, max_chars: int = 250) -> List[str]:
    """Segment text into prosodically complete sentence units.

    Parameters
    ----------
    text:
        Raw text to segment.
    min_chars:
        Minimum characters for a standalone segment before merging with adjacent sentence.
    max_chars:
        Target maximum characters per chunk (soft ceiling).

    Returns
    -------
    List[str]
        Cleaned, prosodically coherent sentence strings.
    """
    if not text or not text.strip():
        return []

    cleaned_text = text.strip()

    # Protect decimals: replace dot in numbers like 3.14 with special placeholder
    DOT = "<DOT>"
    protected = re.sub(r"(\d+)\.(\d+)", rf"\1{DOT}\2", cleaned_text)

    # Protect common dotted abbreviations like e.g. and i.e.
    protected = re.sub(r"\b([eE])\.([gG])\.", rf"\1{DOT}\2{DOT}", protected)
    protected = re.sub(r"\b([iI])\.([eE])\.", rf"\1{DOT}\2{DOT}", protected)
    protected = re.sub(r"\b([vV])\.([sS])\.", rf"\1{DOT}\2{DOT}", protected)
    protected = re.sub(r"\b([eE])\.([tT])\.([cC])\.", rf"\1{DOT}\2{DOT}\3{DOT}", protected)

    # Protect single-letter dotted initials like A.P.I. or J.A.R.V.I.S.
    protected = re.sub(r"\b([A-Za-z])\.(?=[A-Za-z]\.)", rf"\1{DOT}", protected)

    # Split on paragraph breaks or terminal punctuation followed by space
    raw_parts = re.split(r"(?<=[.!?])\s+|\n{2,}", protected)

    sentences: List[str] = []
    current_sentence = ""

    for part in raw_parts:
        part = part.strip()
        if not part:
            continue

        # Restore placeholders
        part_restored = part.replace(DOT, ".")

        # Check if the part ends an abbreviation (e.g. "Dr.")
        last_word = part_restored.split()[-1] if part_restored.split() else ""
        if is_abbreviation(last_word):
            if current_sentence:
                current_sentence += " " + part_restored
            else:
                current_sentence = part_restored
            continue

        if current_sentence:
            current_sentence += " " + part_restored
        else:
            current_sentence = part_restored

        # If current sentence has terminal punctuation and exceeds minimum threshold
        has_terminal = bool(re.search(r"[.!?]$", current_sentence))
        if has_terminal and len(current_sentence) >= min_chars:
            sentences.append(current_sentence.strip())
            current_sentence = ""
        elif len(current_sentence) >= max_chars:
            sentences.append(current_sentence.strip())
            current_sentence = ""

    if current_sentence.strip():
        # If the remaining part is very short, merge with the last sentence if possible
        if sentences and len(current_sentence.strip()) < min_chars:
            sentences[-1] = sentences[-1] + " " + current_sentence.strip()
        else:
            sentences.append(current_sentence.strip())

    return sentences


def stream_sentences(chunks: Iterator[str]) -> Iterator[str]:
    """Incrementally yield complete sentences from an incoming stream of tokens/words."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        # Look for sentence boundaries in accumulated buffer
        sentences = segment_sentences(buffer)
        if len(sentences) > 1:
            # Yield all completed sentences except the last (which may still be growing)
            for s in sentences[:-1]:
                yield s
            buffer = sentences[-1]

    if buffer.strip():
        yield buffer.strip()

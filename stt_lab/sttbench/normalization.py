"""Script-preserving WER normalization; equivalences are explicit and symmetric."""
from __future__ import annotations

import re
import unicodedata


def basic_tokens(text: str) -> list[str]:
    # NFC preserves Devanagari vowel signs and nukta. Never use an ASCII-only
    # regex, strip Unicode marks, transliterate, or discard numbers for WER.
    text = unicodedata.normalize("NFC", text).casefold()
    output = []
    for index, char in enumerate(text):
        category = unicodedata.category(char)
        if category[0] in ("L", "M", "N"):
            output.append(char)
        elif char in (".", ",", ":", "/") and index > 0 and index + 1 < len(text) and text[index-1].isdecimal() and text[index+1].isdecimal():
            output.append(char)
        elif char in ("+", "-", "−", "%", "₹", "$", "€", "£") and (
                (index > 0 and text[index-1].isdecimal()) or
                (index + 1 < len(text) and text[index+1].isdecimal())):
            output.append(char)
        else:
            output.append(" ")
    return "".join(output).split()


def numeric_signature(tokens: list[str] | tuple[str, ...]) -> list[str]:
    return [token for token in tokens if any(char.isdecimal() for char in token)]


class Normalizer:
    def __init__(self, equivalences: dict[str, str] | None = None):
        self.equivalences = equivalences or {}
        if not isinstance(self.equivalences, dict):
            raise ValueError("Equivalences must be an object mapping phrases to canonical phrases")
        self.rules: dict[tuple[str, ...], tuple[str, ...]] = {}
        for source, target in self.equivalences.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise ValueError("Equivalence sources and targets must be strings")
            source_tokens, target_tokens = basic_tokens(source), basic_tokens(target)
            if not source_tokens or not target_tokens:
                raise ValueError("Equivalences cannot erase text")
            if numeric_signature(source_tokens) != numeric_signature(target_tokens):
                raise ValueError("Equivalences may not add, remove, or change numeric tokens")
            key, value = tuple(source_tokens), tuple(target_tokens)
            if key in self.rules and self.rules[key] != value:
                raise ValueError("Conflicting normalized equivalences")
            self.rules[key] = value
        # Require terminal canonical forms; order-dependent chains are misleading.
        for key, value in self.rules.items():
            for other, replacement in self.rules.items():
                if other == replacement:
                    continue
                if any(value[i:i+len(other)] == other for i in range(len(value)-len(other)+1)):
                    raise ValueError("Equivalence targets must be canonical (no chained or cyclic replacements)")
        self.lengths = sorted({len(key) for key in self.rules}, reverse=True)

    def tokens(self, text: str) -> list[str]:
        tokens = basic_tokens(text)
        result: list[str] = []
        index = 0
        while index < len(tokens):
            for size in self.lengths:
                key = tuple(tokens[index:index+size])
                if len(key) == size and key in self.rules:
                    result.extend(self.rules[key])
                    index += size
                    break
            else:
                result.append(tokens[index])
                index += 1
        return result


def word_errors(reference: list[str], hypothesis: list[str]) -> dict:
    # Linear memory edit distance, carrying S/D/I counts for transparent reports.
    previous = [(j, 0, 0, j) for j in range(len(hypothesis) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, actual in enumerate(hypothesis, 1):
            if expected == actual:
                current.append(previous[j - 1])
            else:
                substitution = previous[j - 1]
                deletion = previous[j]
                insertion = current[j - 1]
                candidates = [(substitution[0]+1, substitution[1]+1, substitution[2], substitution[3]),
                              (deletion[0]+1, deletion[1], deletion[2]+1, deletion[3]),
                              (insertion[0]+1, insertion[1], insertion[2], insertion[3]+1)]
                current.append(min(candidates, key=lambda item: item[0]))
        previous = current
    errors, substitutions, deletions, insertions = previous[-1]
    return {"reference_words": len(reference), "hypothesis_words": len(hypothesis),
            "errors": errors, "substitutions": substitutions, "deletions": deletions,
            "insertions": insertions, "wer": errors / len(reference) if reference else None,
            "silence_hallucination_words": len(hypothesis) if not reference else 0}


def entity_counts(entities: list[dict], hypothesis: str, normalizer: Normalizer) -> dict:
    words = normalizer.tokens(hypothesis)
    totals = {"name": {"matched": 0, "reference": 0}, "number": {"matched": 0, "reference": 0}}
    for entity in entities:
        totals[entity["kind"]]["reference"] += 1
        forms = [normalizer.tokens(form) for form in [entity["text"], *entity.get("aliases", [])]]
        if any(form and any(words[i:i+len(form)] == form for i in range(len(words)-len(form)+1)) for form in forms):
            totals[entity["kind"]]["matched"] += 1
    return totals

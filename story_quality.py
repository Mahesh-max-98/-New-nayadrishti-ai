"""Conservative source-grounding and duplicate-detection helpers."""
import re
from difflib import SequenceMatcher

_DIGIT_TRANSLATION = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_story_title(title):
    """Normalize punctuation, whitespace and Devanagari digits for comparisons."""
    value = (title or "").casefold().translate(_DIGIT_TRANSLATION)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def numeric_facts(text):
    """Return normalized numeric facts, retaining decimals and grouped numbers."""
    value = (text or "").translate(_DIGIT_TRANSLATION)
    return {
        re.sub(r"[,،]", "", match)
        for match in re.findall(r"\d+(?:[.,،]\d+)*", value)
    }


def has_unsupported_numbers(generated_text, source_text):
    """True when a generated post introduces a number absent from its source."""
    return not numeric_facts(generated_text).issubset(numeric_facts(source_text))


def similar_story_titles(first, second, similarity_threshold=0.90):
    """Conservative near-duplicate test; conflicting numeric facts stay separate."""
    a = normalize_story_title(first)
    b = normalize_story_title(second)
    if not a or not b or len(a) < 18 or len(b) < 18:
        return False
    if numeric_facts(first) != numeric_facts(second):
        return False
    if SequenceMatcher(None, a, b).ratio() >= similarity_threshold:
        return True
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if len(tokens_a) < 4 or len(tokens_b) < 4:
        return False
    overlap = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    return overlap >= 0.84

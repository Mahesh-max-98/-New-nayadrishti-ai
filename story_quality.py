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


def similar_story_titles(first, second, similarity_threshold=0.88):
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
    return overlap >= 0.80



def editorial_quality_flags(summary, headline="", source_text=""):
    """Return conservative review prompts; these are not an automated fact-check."""
    post = (summary or "").strip()
    source = " ".join(part for part in (headline or "", source_text or "") if part).strip()
    flags = []
    if not source_text or len((source_text or "").strip()) < 40:
        flags.append("मूल समाचारको पर्याप्त पाठ उपलब्ध छैन; शीर्षक र मूल लिंक आफैँ जाँच्नुहोस्।")
    if source and has_unsupported_numbers(post, source):
        flags.append("पोस्टमा स्रोतमा नभेटिएको अंक हुन सक्छ; सबै संख्या मूल समाचारसँग मिलाउनुहोस्।")
    if re.search(r"https?://|www\\.", post, re.IGNORECASE):
        flags.append("पोस्टमा URL देखियो; प्रकाशित गर्नुअघि जाँच्नुहोस्।")
    if "समाचार अपडेट" in post:
        flags.append("पोस्टमा अनावश्यक ‘समाचार अपडेट’ वाक्यांश छ।")
    nonempty_lines = [line for line in post.splitlines() if line.strip()]
    if len(nonempty_lines) > 10:
        flags.append("पोस्टमा १० भन्दा बढी छुट्टाछुट्टै लाइन छन्; आवश्यकताअनुसार सम्पादन गर्नुहोस्।")
    return flags

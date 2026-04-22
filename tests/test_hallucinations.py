"""Standalone test of the whisper hallucination filter."""
import re

_DICTATION_HALLUCINATION_STOPWORDS = {
    "the", "you", "and", "a", "to", "of", "is", "it", "so", "i",
    "uh", "um", "ah", "oh", "mm", "mhm", "hmm", "hm", "eh",
    "thanks", "thank", "bye", "okay", "ok",
}

_DICTATION_TRAILING_HALLUCINATIONS = {"the", "you", "and", "a"}

_WHISPER_STOCK_HALLUCINATIONS = (
    "good afternoon, everyone",
    "good afternoon everyone",
    "good morning, everyone",
    "good morning everyone",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "don't forget to subscribe",
    "bye-bye",
    "bye bye",
)

_WHISPER_STOCK_PATTERNS = tuple(
    re.compile(r"\b" + re.escape(phrase) + r"\.?", re.IGNORECASE)
    for phrase in _WHISPER_STOCK_HALLUCINATIONS
)


def _strip_whisper_hallucinations(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""

    stock_hit = False
    for pattern in _WHISPER_STOCK_PATTERNS:
        new_stripped, n = pattern.subn("", stripped)
        if n > 0:
            stock_hit = True
            stripped = new_stripped
    if stock_hit:
        stripped = re.sub(r"\s+", " ", stripped)
        stripped = re.sub(r"\s*[,.;:!?\-]+\s*$", "", stripped)
        stripped = re.sub(r"(?<=[.!?])\s*[,.;:!?\-]+", "", stripped)
        stripped = stripped.strip()

    if not stripped:
        return ""

    tokens = stripped.split()

    def _norm(tok):
        return tok.lower().strip(".,!?;:\"'")

    filtered = []
    for tok in tokens:
        rstripped = tok.rstrip(".,!?;:\"'")
        if len(rstripped) >= 2 and rstripped.endswith("-") and not rstripped.endswith("--"):
            continue
        filtered.append(tok)
    tokens = filtered

    cleaned = [t for t in (_norm(tok) for tok in tokens) if t]
    if cleaned and len(cleaned) <= 2 and all(tok in _DICTATION_HALLUCINATION_STOPWORDS for tok in cleaned):
        return ""

    deduped = []
    for tok in tokens:
        key = _norm(tok)
        if deduped and key and key == _norm(deduped[-1]):
            continue
        deduped.append(tok)
    tokens = deduped

    while len(tokens) >= 3:
        tail = _norm(tokens[-1])
        if tail in _DICTATION_TRAILING_HALLUCINATIONS:
            tokens.pop()
        else:
            break
    return " ".join(tokens).strip()


cases = [
    # (input, expected, description)
    ("Thank you", "", "pure 2-word stopword"),
    ("the", "", "single stopword"),
    ("you the", "", "two stopwords"),
    ("Thank you.", "", "with punctuation"),

    ("The meeting is at noon", "The meeting is at noon", "legitimate leading 'The'"),
    ("I want to dictate", "I want to dictate", "legitimate 4 words starting with stopwords"),
    ("A quick test", "A quick test", "legitimate leading 'A'"),

    ("pretty good so far the", "pretty good so far", "trailing 'the' stripped"),
    ("hello you", "hello you", "2-word legitimate keep (not all stopwords? wait 'you' is stopword but 'hello' isn't)"),
    ("it is a test the", "it is a test", "trailing 'the' from 5 words"),

    ("test test test test test", "test", "5-fold consecutive repeat"),
    ("playing playing", "playing", "double repeat"),
    ("Their daughter is playing playing", "Their daughter is playing", "trailing duplicate"),
    ("very very good", "very good", "legitimate 'very very' collapsed (acceptable tradeoff)"),

    ("The meeting is at noon Good afternoon, everyone.", "The meeting is at noon", "whisper stock phrase stripped"),
    ("Good afternoon, everyone.", "", "pure stock phrase"),
    ("thanks for watching", "", "pure stock phrase alt"),
    ("This is real. Thanks for watching!", "This is real.", "stock phrase mid-text"),
    ("Please subscribe to my channel", "to my channel", "partial stock phrase"),

    ("Let's meet at four two", "Let's meet at four two", "non-stopword trailing word kept (limitation)"),

    ("", "", "empty"),
    ("   ", "", "whitespace only"),
    ("Alright, let's test it.", "Alright, let's test it.", "legitimate full sentence"),

    ("Okay, let's test this new dicta- indication method.", "Okay, let's test this new indication method.", "hyphen fragment dropped"),
    ("dicta-", "", "pure hyphen fragment (2 words min check... actually 1 token)"),
    ("well- maybe", "maybe", "fragment + word"),
    ("state-of-the-art", "state-of-the-art", "legitimate hyphenated compound preserved"),
    ("a- b- c- d", "d", "multiple fragments"),
]

passes = 0
fails = 0
for inp, expected, desc in cases:
    got = _strip_whisper_hallucinations(inp)
    ok = got == expected
    status = "PASS" if ok else "FAIL"
    if ok:
        passes += 1
    else:
        fails += 1
    marker = "  " if ok else "!!"
    print(f"{marker} {status}: {desc}")
    print(f"     in:  {inp!r}")
    print(f"     out: {got!r}")
    if not ok:
        print(f"     exp: {expected!r}")

print()
print(f"{passes} passed, {fails} failed, {passes + fails} total")

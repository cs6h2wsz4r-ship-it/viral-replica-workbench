"""Transcript evidence helpers and Hypit's speech-duration estimate (`hypit estimate`)."""
from __future__ import annotations

import csv
import io
import math
import re

# @hypit/speech-estimate pace table (units per second)
PACE_RATE = {
    "zh": {"slow": 4.2, "normal": 5.25, "fast": 6.5625},
    "en": {"slow": 4.2, "normal": 4.6, "fast": 5.6},
    "ja": {"slow": 6.0, "normal": 7.5, "fast": 9.375},
    "es": {"slow": 4.72, "normal": 5.9, "fast": 7.375},
}
_HAN = re.compile(r"[㐀-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
_DIGITS = re.compile(r"\d")


def _english_syllables(word: str) -> int:
    w = word.lower()
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(1, n)


def speech_units(text: str, language: str = "zh") -> int:
    """Han chars count 1 each; embedded English words by syllables; digits ~1 each (spell them out to be exact)."""
    han = len(_HAN.findall(text))
    latin = sum(_english_syllables(w) for w in _LATIN_WORD.findall(text))
    digits = len(_DIGITS.findall(text))
    return han + latin + digits


def estimate_seconds(text: str, language: str = "zh", pace: str = "normal", padding: float = 0.0) -> float:
    lang = language if language in PACE_RATE else "zh"
    rate = PACE_RATE[lang].get(pace, PACE_RATE[lang]["normal"])
    return speech_units(text, lang) / rate + padding


def suggest_duration(seconds: float, low: int = 4, high: int = 15) -> int:
    return int(min(high, max(low, math.ceil(seconds))))


def all_words(transcript: dict | None) -> list[dict]:
    out = []
    for seg in (transcript or {}).get("segments", []):
        for w in seg.get("words") or []:
            if w.get("start") is not None and w.get("word"):
                out.append(w)
    return out


def words_between(transcript: dict | None, start: float, end: float) -> str:
    words = [w["word"] for w in all_words(transcript) if w["start"] < end and (w.get("end") or w["start"]) > start]
    if words:
        joined = "".join(words) if _HAN.search("".join(words)) else " ".join(words)
        return joined.strip()
    segs = [s["text"] for s in (transcript or {}).get("segments", []) if s["start"] < end and s["end"] > start]
    return " ".join(segs)


def dialogue_between(transcript: dict | None, start: float, end: float, *, midpoint_ownership: bool = False) -> str:
    """Keep only majority-overlapping words, using sentence text for original typography.

    Word alignment may strip spaces and punctuation. Locate words in the original
    sentence to retain those characters without borrowing any unselected words.
    Unlike frame labels, dialogue must never fall back to an untimed sentence.
    New scene scripts can assign word midpoints to half-open intervals so a word
    exactly bisected by a cut belongs to the right segment instead of vanishing.
    """
    parts = []
    for seg in (transcript or {}).get("segments", []):
        words = seg.get("words") or []
        text = seg.get("text") or ""
        spans, selected, cursor = [], [], 0
        for word in words:
            token = str(word.get("word") or "").strip()
            pos = text.find(token, cursor) if token else -1
            spans.append((pos, pos + len(token)))
            if pos >= 0:
                cursor = pos + len(token)
            try:
                a, b = float(word["start"]), float(word["end"])
                overlap = max(0.0, min(end, b) - max(start, a))
                owned = (b >= a and start <= (a+b)/2 < end) if midpoint_ownership else (
                    overlap > (b - a) / 2 if b > a else b == a and start <= a < end)
                selected.append(bool(token) and math.isfinite(a) and math.isfinite(b) and owned)
            except (KeyError, TypeError, ValueError):
                selected.append(False)
        if not any(selected):
            continue
        if all(a >= 0 for a, _ in spans):
            chunks = []
            for i, keep in enumerate(selected):
                if keep:
                    a = 0 if i == 0 else spans[i][0]
                    b = spans[i + 1][0] if i + 1 < len(spans) else len(text)
                    chunks.append(text[a:b])
            parts.append("".join(chunks).strip())
        else:
            tokens = [w["word"].strip() for w, keep in zip(words, selected) if keep]
            joined = "".join(tokens) if _HAN.search("".join(tokens)) else " ".join(tokens)
            parts.append(re.sub(r"\s+([,.;:!?，。！？；：])", r"\1", joined))
    joined = ""
    for part in parts:
        if joined and part and not (_HAN.search(joined[-1:]) or _HAN.search(part[:1]) or
                                    joined[-1:] in "，。！？；：、"):
            joined += " "
        joined += part
    return joined


def word_labels(transcript: dict | None, times: list[float], every: float) -> list[str]:
    """Words being spoken around each sampled frame (the `--transcript` label under a tile cell)."""
    return [words_between(transcript, t - every / 2, t + every / 2) for t in times]


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(transcript: dict | None) -> str:
    lines = []
    for i, seg in enumerate((transcript or {}).get("segments", []), start=1):
        lines += [str(i), f"{_ts(seg['start'])} --> {_ts(seg['end'])}", seg["text"], ""]
    return "\n".join(lines)


def to_word_csv(transcript: dict | None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sentence", "word", "start", "end"])
    for i, seg in enumerate((transcript or {}).get("segments", []), start=1):
        for word in seg.get("words") or []:
            w.writerow([i, word.get("word"), word.get("start"), word.get("end")])
    return buf.getvalue()


def hypit_transcript(transcript: dict | None) -> dict:
    """Portable view similar to hypit.transcript@1: flat words with seconds plus sentence passages."""
    t = transcript or {}
    return {"format": "hypit.transcript@1-compatible", "engine": t.get("engine"), "language": t.get("language"),
            "timing": t.get("timing"),
            "passages": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in t.get("segments", [])],
            "words": [{"text": w["word"], "start": w["start"], "end": w.get("end")} for w in all_words(t)]}

"""Rules-first FAQ retrieval.

Keyword/tag scoring over data/faq.json. Returns the best entry and a confidence
score. The bot answers from rules when confidence is high; only low-confidence
messages get escalated to the LLM (or a human). This split is what we measure:
how many questions the rules alone close, vs how many need the LLM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent.parent / "data" / "faq.json"


# common Ukrainian/Russian function words that carry no topic signal
_STOPWORDS = {
    "яка", "який", "яке", "які", "що", "як", "чи", "для", "при", "про", "над",
    "під", "там", "тут", "цей", "ця", "це", "ці", "той", "так", "теж", "вже",
    "можна", "треба", "потрібно", "потрібні", "потрібен", "хочу", "буде", "був",
    "день", "днів", "ваш", "ваша", "ваші", "мені", "мене", "вам", "нам", "его",
    "или", "что", "как", "для", "это", "авто", "машину", "машина", "взяти",
}


def _stems(s: str) -> set[str]:
    """Topic tokens reduced to a 5-char stem so Ukrainian inflections collapse
    (документи/документ → 'докум', кілометрів/кілометраж → 'кіломе')."""
    # short but meaningful domain tokens we keep despite the >=3 length rule
    _KEEP_SHORT = {"км", "то", "пдв"}
    out: set[str] = set()
    for t in re.findall(r"\w+", s.lower(), re.UNICODE):
        if t in _STOPWORDS:
            continue
        if len(t) < 3 and t not in _KEEP_SHORT:
            continue
        out.add(t[:5])
    return out


@dataclass
class FaqEntry:
    id: str
    question: str
    answer: str
    tags: list[str]


@dataclass
class FaqMatch:
    entry: FaqEntry
    score: float  # 0..1 confidence


class FaqBook:
    # below this, rules are not confident enough — escalate to LLM/human
    CONFIDENCE_FLOOR = 0.34

    def __init__(self, path: Path = _DATA):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.entries: list[FaqEntry] = [
            FaqEntry(id=e["id"], question=e["question"], answer=e["answer"], tags=e["tags"])
            for e in raw["entries"]
        ]
        # Tags are curated keywords (strong signal); question words are weak signal.
        # Index them separately so confidence comes from TAG hits, not message length.
        self._tag_idx: list[set[str]] = []
        self._q_idx: list[set[str]] = []
        for e in self.entries:
            tag_stems: set[str] = set()
            for tag in e.tags:
                tag_stems |= _stems(tag)
            self._tag_idx.append(tag_stems)
            self._q_idx.append(_stems(e.question))

    def match(self, message: str) -> Optional[FaqMatch]:
        msg_tokens = _stems(message)
        if not msg_tokens:
            return None
        best_i, best_tag, best_q = -1, 0, 0
        for i in range(len(self.entries)):
            tag_hits = len(msg_tokens & self._tag_idx[i])
            q_hits = len(msg_tokens & self._q_idx[i])
            # rank by tag hits first, then question hits
            if (tag_hits, q_hits) > (best_tag, best_q):
                best_i, best_tag, best_q = i, tag_hits, q_hits
        if best_i < 0 or (best_tag == 0 and best_q < 2):
            return None
        # one curated tag hit is already a confident match; extra hits raise it.
        if best_tag:
            score = min(1.0, 0.5 + 0.15 * (best_tag - 1) + 0.05 * best_q)
        else:
            score = 0.35  # only weak question-word overlap → just escalate-able
        return FaqMatch(entry=self.entries[best_i], score=round(score, 3))

    def answer(self, message: str) -> Optional[FaqMatch]:
        m = self.match(message)
        if m and m.score >= self.CONFIDENCE_FLOOR:
            return m
        return None


if __name__ == "__main__":
    book = FaqBook()
    tests = [
        "Яка застава на бізнес авто?",
        "які документи треба щоб взяти машину",
        "можна виїхати в польщу?",
        "скільки кілометрів на день можна",
        "а ви возите дитяче крісло",
        "яка погода завтра",  # should NOT match confidently
    ]
    for t in tests:
        m = book.match(t)
        if m:
            tag = "✓" if m.score >= FaqBook.CONFIDENCE_FLOOR else "↑ escalate"
            print(f"[{m.score:.2f} {tag}] {t!r} → {m.entry.id}")
        else:
            print(f"[—] {t!r} → no match")

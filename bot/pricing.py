"""Deterministic price calculator for a car-rental company.

Rules-only. No LLM, no network. Given a car and a number of days it returns the
exact price using the tariff grid. The duration bracket (longer
rent = cheaper per day) is the one thing a human manager applies by hand today —
here it is a pure function.

Car-name matching is token-based with a small Cyrillic→Latin alias map, because
users type 'камрі', 'ауді', 'бмв' while the fleet is stored in Latin. This alias
map is the cheap rules layer; the messy long tail ('тачка як у бонда') is exactly
where the LLM earns its place later — see docs/plan.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent.parent / "data" / "tariffs.json"

# Upper day bound for each tariff column (tariffs.json["duration_brackets"]).
_BRACKET_MAX_DAYS = (3, 9, 25, 10**9)

# Cyrillic spellings users type → the Latin token that appears in the model name.
_ALIASES = {
    "камрі": "camry", "камри": "camry", "тойота": "toyota", "корола": "corolla",
    "королла": "corolla", "ленд": "land", "крузер": "cruiser", "рав": "rav4",
    "ауді": "audi", "ауди": "audi", "бмв": "bmw", "мерседес": "mercedes",
    "мерс": "mercedes", "шкода": "skoda", "октавія": "octavia", "октавия": "octavia",
    "гольф": "golf", "джетта": "jetta", "поло": "polo", "фольксваген": "volkswagen",
    "тігуан": "tiguan", "туарег": "touareg", "логан": "logan", "рено": "renault",
    "дастер": "duster", "акцент": "accent", "хюндай": "hyundai", "хундай": "hyundai",
    "елантра": "elantra", "мазда": "mazda", "хонда": "honda", "цивік": "civic",
    "акорд": "accord", "інфініті": "infiniti", "инфинити": "infiniti",
    "лексус": "lexus", "пежо": "peugeot", "сітроен": "citroen", "ситроен": "citroen",
    "сеат": "seat", "ібіца": "ibiza", "фієста": "fiesta", "фиеста": "fiesta",
    "гібрид": "hybrid", "гибрид": "hybrid",
}

# Generic model words that don't identify a car on their own.
_GENERIC = {"new", "sedan", "hatchback", "at", "ii", "iv", "vi", "vii", "viii",
            "matic", "4matic", "line", "benz"}


def _tokenize(s: str) -> set[str]:
    """Lowercase, map Cyrillic aliases to Latin, return alnum tokens (len>=2)."""
    out: set[str] = set()
    for raw in re.findall(r"\w+", s.lower(), re.UNICODE):
        tok = _ALIASES.get(raw, raw)
        if len(tok) >= 2:
            out.add(tok)
    return out


def _model_keys(model: str) -> set[str]:
    """Distinctive Latin tokens of a model name (brand/model, not generic trim)."""
    toks = {t for t in re.findall(r"[a-z0-9]+", model.lower()) if len(t) >= 2}
    # keep distinctive tokens: alpha words (len>=3) AND short model codes with a
    # digit (x5, q5, a4, t5) — those disambiguate trims like Audi A4 vs Q5.
    keys = {t for t in toks
            if t not in _GENERIC and (len(t) >= 3 or any(c.isdigit() for c in t))}
    return keys or toks  # fall back to all tokens if everything was generic


def deposit_str(deposit: Optional[int]) -> str:
    """Render a deposit, guarding incomplete data so customers never see 'None€'."""
    return f"{deposit}€" if deposit is not None else "уточнить менеджер"


@dataclass
class Car:
    model: str
    car_class: str
    prices: list[int]
    deposit: Optional[int]


@dataclass
class Quote:
    car: Car
    days: int
    bracket: str
    price_per_day: int
    total: int
    deposit: Optional[int]


class PriceBook:
    def __init__(self, path: Path = _DATA):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.brackets: list[str] = raw["duration_brackets"]
        self.cars: list[Car] = []
        for car_class, rows in raw["classes"].items():
            for row in rows:
                self.cars.append(Car(row["model"], car_class, row["prices"], row.get("deposit")))
        self._keys = [_model_keys(c.model) for c in self.cars]

    def _bracket_index(self, days: int) -> int:
        for i, hi in enumerate(_BRACKET_MAX_DAYS):
            if days <= hi:
                return i
        return len(_BRACKET_MAX_DAYS) - 1

    def find_matches(self, query: str) -> list[Car]:
        """All cars sharing the top match score (>0). Used to disambiguate a
        brand-only query like 'БМВ' → several models → ask which one."""
        q = _tokenize(query)
        if not q:
            return []
        scored = [(len(keys & q), car) for car, keys in zip(self.cars, self._keys)]
        best = max((s for s, _ in scored), default=0)
        if best == 0:
            return []
        return [car for s, car in scored if s == best]

    def find_car(self, query: str) -> Optional[Car]:
        matches = self.find_matches(query)
        return matches[0] if matches else None

    def quote(self, query: str, days: int) -> Optional[Quote]:
        if days < 1:
            raise ValueError("days must be >= 1")
        car = self.find_car(query)
        if car is None:
            return None
        idx = self._bracket_index(days)
        ppd = car.prices[idx]
        return Quote(car, days, self.brackets[idx], ppd, ppd * days, car.deposit)


if __name__ == "__main__":
    book = PriceBook()
    for q, d in [("камрі гібрид на 5 днів", 5), ("логан", 2), ("ауді на тиждень", 7),
                 ("bmw x5 2026", 30), ("Skoda Octavia", 12), ("хочу мерседес", 3)]:
        res = book.quote(q, d)
        if res:
            print(f"{q!r:28} → {res.car.model} ({res.car.car_class}), {d}дн = "
                  f"{res.price_per_day}€/доба = {res.total}€, застава {res.deposit}€")
        else:
            print(f"{q!r:28} → не знайдено")

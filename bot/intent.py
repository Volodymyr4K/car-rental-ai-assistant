"""Intent router — the orchestrator (rules-first).

Decides what a message wants and produces a reply using the cheapest competent
layer. Order of precedence (high-signal → low):

  1. PRICE   — message names a car and/or asks cost → deterministic calculator
  2. FAQ     — confident keyword match → canonical answer from rules
  3. BOOKING — wants to rent → collect slots, hand a warm lead to a manager
  4. ESCALATE— none of the above confidently → LLM (if wired) or human

The LLM is injected, not imported. With no LLM key the router still works fully on
rules; unmatched messages just escalate to a human. That is the graceful-degrade
promise: the bot is useful at $0, the LLM only widens coverage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from .booking import _BOOKING_WORDS, _extract_days  # single source of truth
from .faq import FaqBook
from .pricing import PriceBook, Quote, deposit_str

# words that signal the user is asking about price
_PRICE_WORDS = {"ціна", "ціну", "вартість", "вартости", "коштує", "почім", "порахуй",
                "порахувати", "скільки", "прайс", "тариф"}


# --- customer-side triggers used before the sales flow (importable + testable) ---
HUMAN_WORDS = {"оператор", "оператора", "оператором", "менеджер", "менеджера",
               "менеджером", "людина", "людину", "людиною", "живу", "жива"}
COMPLAINT_WORDS = {"скарга", "скаргу", "поверніть", "верніть", "повернення",
                   "шахраї", "шахрай", "обман", "ошукали", "жахливо", "обурений",
                   "неприйнятно", "розбили"}
ACCIDENT_WORDS = {"дтп", "аварія", "аварії", "аварію", "врізався", "врізалась",
                  "врізалися", "розбив", "зіткнення", "збив", "наїхав"}


def _words(message: str) -> set:
    return set(re.findall(r"\w+", message.lower()))


def is_accident(message: str) -> bool:
    return bool(_words(message) & ACCIDENT_WORDS)


def handoff_reasons(message: str) -> list:
    """Explicit human request / complaint — caught before any sales logic."""
    w = _words(message)
    reasons = []
    if w & HUMAN_WORDS:
        reasons.append("клієнт просить менеджера")
    if w & COMPLAINT_WORDS:
        reasons.append("можлива скарга / негатив")
    return reasons


def accident_reply(phone: str) -> str:
    """Fixed safety response — NEVER hand an accident to the LLM (it invents numbers)."""
    return ("Найперше — переконайтесь, що всі цілі. За потреби викличте екстрені "
            f"служби: 112. Потім одразу зателефонуйте нам: {phone}. "
            "Авто застраховане — ми все вирішимо, не хвилюйтесь.")


@dataclass
class Reply:
    text: str
    intent: str                  # price | faq | booking | escalate
    source: str                  # rules | llm | human
    confidence: float
    quote: Optional[dict] = None
    outcome: str = "answered"    # answered | lead_captured | escalated


class Router:
    def __init__(
        self,
        price_book: Optional[PriceBook] = None,
        faq_book: Optional[FaqBook] = None,
        llm: Optional[Callable[[str], str]] = None,
    ):
        self.prices = price_book or PriceBook()
        self.faq = faq_book or FaqBook()
        self.llm = llm  # callable(message) -> str, or None

    def _try_price(self, message: str) -> Optional[Reply]:
        car = self.prices.find_car(message)
        asks_price = bool(set(re.findall(r"\w+", message.lower())) & _PRICE_WORDS)
        if not car and not asks_price:
            return None
        days = _extract_days(message)
        if car and days:
            q: Quote = self.prices.quote(car.model, days)
            txt = (
                f"{q.car.model} ({q.car.car_class}) на {days} дн:\n"
                f"💶 {q.price_per_day}€/доба × {days} = {q.total}€\n"
                f"🔒 застава {deposit_str(q.deposit)}\n"
                f"Ціна включає страховку, 250 км/добу, підтримку 24/7. "
                f"Оформити?"
            )
            return Reply(txt, "price", "rules", 0.9,
                         quote={"car": q.car.model, "days": days,
                                "total": q.total, "deposit": q.deposit})
        if car and not days:
            return Reply(f"На скільки діб потрібна {car.model}? Підкажу точну ціну.",
                         "price", "rules", 0.6)
        # asks price but no car identified
        return Reply("Яке авто вас цікавить і на скільки діб? Порахую вартість.",
                     "price", "rules", 0.5)

    def _try_faq(self, message: str) -> Optional[Reply]:
        m = self.faq.answer(message)
        if m:
            return Reply(m.entry.answer, "faq", "rules", m.score)
        return None

    def _try_booking(self, message: str) -> Optional[Reply]:
        words = set(re.findall(r"\w+", message.lower()))
        if not (words & _BOOKING_WORDS):
            return None
        txt = ("Чудово, оформимо! Підкажіть, будь ласка:\n"
               "• місто й місце видачі\n• авто (або клас)\n• дати з/по\n• ваше ім'я та телефон\n"
               "Передам менеджеру — він підтвердить наявність.")
        return Reply(txt, "booking", "rules", 0.7, outcome="lead_captured")

    def _escalate(self, message: str) -> Reply:
        if self.llm:
            answer = self.llm(message)
            if answer:  # LLM may return None on timeout/failure → fall back to human
                return Reply(answer, "escalate", "llm", 0.3)
        return Reply("Передаю ваше питання менеджеру — він зв'яжеться з вами найближчим часом.",
                     "escalate", "human", 0.0, outcome="escalated")

    def handle(self, message: str) -> Reply:
        # booking intent beats a bare price word ("хочу орендувати камрі на 3 дні"
        # should still quote, so price is checked first only when days+car present)
        price = self._try_price(message)
        if price and price.confidence >= 0.9:      # full quote -> strongest
            return price
        booking = self._try_booking(message)
        if booking:
            return booking
        # a confident FAQ beats a bare price-word with no car ("скільки км на день")
        faq = self._try_faq(message)
        if faq:
            return faq
        if price:                                  # partial price (needs a slot)
            return price
        return self._escalate(message)


if __name__ == "__main__":
    r = Router()
    convo = [
        "Скільки коштує камрі гібрид на 5 днів?",
        "яка застава на бізнес клас?",
        "хочу орендувати ауді на тиждень",
        "а можна виїхати в польщу?",
        "скільки км на день включено",
        "чи приймаєте оплату карткою",
        "а ви миєте машину перед видачею з парфумом?",  # off-topic -> escalate
    ]
    for msg in convo:
        rep = r.handle(msg)
        print(f"\n👤 {msg}\n🤖 [{rep.intent}/{rep.source} {rep.confidence}] {rep.text}")

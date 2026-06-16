"""Stateful booking dialog — slot-filling.

Holds a 'booking card' per user and asks only for the EMPTY fields, echoing what
it already understood. This is what makes it feel like a conversation instead of a
static form dump. Rules fill the easy slots (car, explicit days, city, phone); the
messy free-text parts ('на завтра', 'до кінця місяця', vague model) are the LLM
upgrade — see docs/plan.md.

Slot order: car → duration → (price quote) → city → name+phone → confirm → lead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .pricing import Car, PriceBook, deposit_str

# Cyrillic city spellings users type → canonical name (from the company locations).
_CITIES = {
    "київ": "Київ", "києві": "Київ", "києва": "Київ", "киев": "Київ", "киеве": "Київ",
    "харків": "Харків", "харкові": "Харків", "харкова": "Харків", "харьков": "Харків",
    "дніпро": "Дніпро", "дніпрі": "Дніпро", "дніпра": "Дніпро", "днепр": "Дніпро",
    "львів": "Львів", "львові": "Львів", "львова": "Львів", "львов": "Львів",
    "одеса": "Одеса", "одесі": "Одеса", "одеси": "Одеса", "одесса": "Одеса",
    "вінниця": "Вінниця", "вінниці": "Вінниця", "винница": "Вінниця",
    "запоріжжя": "Запоріжжя", "запоріжжі": "Запоріжжя", "запорожье": "Запоріжжя",
    "франківськ": "Івано-Франківськ", "франківську": "Івано-Франківськ",
    "франковск": "Івано-Франківськ", "франківська": "Івано-Франківськ",
    "миколаїв": "Миколаїв", "миколаєві": "Миколаїв", "николаев": "Миколаїв",
    "чернівці": "Чернівці", "чернівцях": "Чернівці", "черновцы": "Чернівці",
    "ужгород": "Ужгород", "ужгороді": "Ужгород",
    "варшава": "Варшава", "варшаві": "Варшава", "варшаву": "Варшава",
}

_BOOKING_WORDS = {"забронювати", "бронь", "бронювання", "орендувати", "оренда",
                  "взяти", "хочу", "потрібне", "потрібно", "замовити", "оформити",
                  "давай", "так"}


# class names the booking prompt advertises → canonical class in tariffs.json
_CLASS_WORDS = {
    "економ": "Економ", "эконом": "Економ",
    "середній": "Середній", "средний": "Середній", "серед": "Середній",
    "бізнес": "Бізнес", "бизнес": "Бізнес",
    "преміум": "Преміум", "премиум": "Преміум", "преміал": "Преміум",
    "позашляховик": "Позашляховик", "джип": "Позашляховик", "кросовер": "Позашляховик",
    "всюдихід": "Позашляховик", "suv": "Позашляховик",
}


def _detect_class(message: str) -> Optional[str]:
    for tok in re.findall(r"\w+", message.lower()):
        if tok in _CLASS_WORDS:
            return _CLASS_WORDS[tok]
    return None


# clear "I'm cancelling" signals — let the user escape the booking loop. Kept
# conservative: only unambiguous cancel words, so "економ не цікавить, хочу бізнес"
# (a class switch) or "забудь про той, покажи інший" are NOT mistaken for a cancel.
_DROP_WORDS = {"подумаю", "передумав", "передумала", "скасуй", "скасувати",
              "відмінити", "відміна", "відмовляюсь", "відмовляюся"}


def _is_drop(message: str) -> bool:
    return bool(set(re.findall(r"\w+", message.lower())) & _DROP_WORDS)


def wants_booking(message: str, has_car: bool) -> bool:
    words = set(re.findall(r"\w+", message.lower()))
    return bool(words & _BOOKING_WORDS) or has_car


def _extract_days(message: str) -> Optional[int]:
    m = message.lower()
    w = re.search(r"(\d+)\s*тижн", m)
    if w:
        return int(w.group(1)) * 7
    d = re.search(r"(\d+)\s*(дн|діб|доб|день)", m)
    if d:
        return int(d.group(1))
    # range like "з 5 по 9" → 4 days (best effort)
    rng = re.search(r"\b(\d{1,2})\D{1,6}(\d{1,2})\b", m)
    if rng and "тижн" not in m:
        a, b = int(rng.group(1)), int(rng.group(2))
        if 1 <= a <= 31 and 1 <= b <= 31 and b > a:
            return b - a
    return None


def _extract_date(message: str) -> Optional[str]:
    m = message.lower()
    for word in ("післязавтра", "завтра", "сьогодні"):
        if word in m:
            return word
    d = re.search(r"\b(\d{1,2}[.\-/]\d{1,2})\b", m)
    return d.group(1) if d else None


def _extract_city(message: str) -> Optional[str]:
    for tok in re.findall(r"\w+", message.lower()):
        if tok in _CITIES:
            return _CITIES[tok]
    return None


def _extract_phone(message: str) -> Optional[str]:
    m = re.search(r"(\+?\d[\d\s\-()]{8,}\d)", message)
    return re.sub(r"[\s\-()]", "", m.group(1)) if m else None


@dataclass
class Booking:
    car: Optional[str] = None
    car_class: Optional[str] = None
    days: Optional[int] = None
    date_from: Optional[str] = None
    city: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    quoted: bool = False
    options: list[str] = field(default_factory=list)  # pending car choices
    awaiting: Optional[str] = None  # 'choice' | 'contact' | None
    done: bool = False


@dataclass
class Step:
    text: str
    booking: Booking
    outcome: str = "in_progress"  # in_progress | lead_captured


class BookingFlow:
    def __init__(self, prices: Optional[PriceBook] = None):
        self.prices = prices or PriceBook()

    def _set_car(self, b: Booking, car: Car) -> None:
        b.car, b.car_class = car.model, car.car_class
        b.options = []
        b.awaiting = None

    def _fill(self, b: Booking, msg: str) -> None:
        """Opportunistically pull any slots out of this message."""
        # resolve a pending car choice via the real token matcher, limited to options
        if b.awaiting == "choice" and b.options:
            cand = [c for c in self.prices.find_matches(msg) if c.model in b.options]
            if len(cand) == 1:
                self._set_car(b, cand[0])
            elif len(cand) > 1:
                b.options = [c.model for c in cand]  # narrowed, still ambiguous
        if not b.car:
            matches = self.prices.find_matches(msg)
            if len(matches) == 1:
                self._set_car(b, matches[0])
            elif len(matches) > 1:
                b.options = [c.model for c in matches]
                b.awaiting = "choice"
            else:
                # no model, but a class name ("бізнес", "позашляховик") → list its models
                cls = _detect_class(msg)
                if cls:
                    b.options = [c.model for c in self.prices.cars if c.car_class == cls]
                    b.awaiting = "choice"
        if b.days is None:
            b.days = _extract_days(msg)
        if not b.date_from:
            b.date_from = _extract_date(msg)
        if not b.city:
            b.city = _extract_city(msg)
        if b.awaiting == "contact":
            ph = _extract_phone(msg)
            if ph:
                b.phone = ph
                name = re.sub(r"\+?\d[\d\s\-()]{8,}\d", "", msg).strip(" ,.-")
                if name and not b.name:
                    b.name = name
            elif not b.name:
                b.name = msg.strip()

    def step(self, b: Booking, msg: str) -> Step:
        # let the customer escape the booking loop instead of being trapped
        if _is_drop(msg):
            b.done = True
            return Step("Гаразд, без проблем! Звертайтесь, коли будете готові. 🙂",
                        b, outcome="cancelled")
        self._fill(b, msg)

        # 1. disambiguate car
        if b.awaiting == "choice" and b.options:
            opts = ", ".join(b.options)
            return Step(f"Яка саме модель? Доступні: {opts}.", b)

        # 2. car
        if not b.car:
            return Step("Яке авто або клас вас цікавить? (економ / середній / бізнес / "
                        "преміум / позашляховик, або конкретна модель)", b)

        # 3. duration
        if b.days is None:
            known = f"Зрозумів: {b.car}" + (f", з {b.date_from}" if b.date_from else "") + ".\n"
            return Step(known + "На скільки діб потрібне авто (або до якого числа)?", b)

        # 4. price quote (once)
        if not b.quoted:
            q = self.prices.quote(b.car, b.days)
            b.quoted = True
            when = f"з {b.date_from} " if b.date_from else ""
            return Step(
                f"{b.car} ({b.car_class}) {when}на {b.days} дн:\n"
                f"💶 {q.price_per_day}€/доба × {b.days} = {q.total}€\n"
                f"🔒 застава {deposit_str(q.deposit)} · включено страховку, 250 км/добу, 24/7.\n"
                + ("У якому місті вам зручно отримати авто?" if not b.city
                   else "Залиште ім'я та телефон — менеджер підтвердить наявність."),
                b,
            )

        # 5. city
        if not b.city:
            return Step("У якому місті отримуєте авто?", b)

        # 6. contact
        if not (b.name and b.phone):
            b.awaiting = "contact"
            miss = "ім'я та телефон" if not (b.name or b.phone) else (
                "телефон" if not b.phone else "ім'я")
            return Step(f"Майже готово! Залиште, будь ласка, {miss}.", b)

        # 7. done → warm lead
        b.done = True
        b.awaiting = None
        q = self.prices.quote(b.car, b.days)
        return Step(
            f"Дякую, {b.name}! Заявку прийнято:\n"
            f"• {b.car} ({b.car_class})\n"
            f"• {b.city}" + (f", з {b.date_from}" if b.date_from else "") + f", {b.days} дн\n"
            f"• орієнтовно {q.total}€ (застава {deposit_str(q.deposit)})\n"
            f"Менеджер зв'яжеться з вами за номером {b.phone} найближчим часом "
            f"і підтвердить наявність. 🚗",
            b, outcome="lead_captured",
        )


if __name__ == "__main__":
    flow = BookingFlow()
    b = Booking()
    convo = ["Хочу БМВ на завтра", "X5 2026", "на 5 днів", "у Києві",
             "Володимир, 0501234567"]
    for msg in convo:
        s = flow.step(b, msg)
        print(f"\n👤 {msg}\n🤖 {s.text}")
        b = s.booking
        if b.done:
            print(f"   ✅ LEAD: {b}")

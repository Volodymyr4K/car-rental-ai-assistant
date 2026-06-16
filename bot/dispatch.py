"""Customer-message decision logic — pure, transport-free, testable.

Both the Telegram handler (bot/bot.py) and the CLI simulator (bot/cli.py) call
`dispatch()` so the routing lives in ONE place (previously duplicated). It is a
plain sync function: callers own where the Booking state is stored (Telegram
user_data vs a local var) and run it in an executor if the LLM might be called.

Order: continue an active booking → (booking word? but a confident FAQ with no car
wins) → one-off router (price / FAQ / escalate-to-LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .booking import Booking, BookingFlow, wants_booking
from .intent import Router


@dataclass
class Decision:
    text: str
    intent: str
    source: str
    confidence: float
    quote: Optional[dict]
    outcome: str
    booking: Optional[Booking]   # state to persist for next turn (None = no/finished)
    lead: Optional[Booking]      # set only when a lead was just captured (card update)


def _from_booking(step) -> Decision:
    b = step.booking
    lead = b if (b.done and step.outcome == "lead_captured") else None
    return Decision(step.text, "booking", "rules", 0.8, None, step.outcome,
                    None if b.done else b, lead)


def dispatch(msg: str, state: Optional[Booking],
             router: Router, flow: BookingFlow) -> Decision:
    # 1. mid-booking → keep slot-filling
    if state and not state.done:
        return _from_booking(flow.step(state, msg))

    # 2. booking intent — BUT a confident FAQ with no car mentioned should answer
    #    ("хочу дізнатися про страховку" is a question, not a booking)
    if wants_booking(msg, has_car=False):
        faq = router.faq.answer(msg)
        has_car = bool(router.prices.find_matches(msg))
        if faq and not has_car:
            return Decision(faq.entry.answer, "faq", "rules", faq.score,
                            None, "answered", None, None)
        return _from_booking(flow.step(Booking(), msg))

    # 3. one-off question: price / FAQ / escalate (may call the LLM)
    rep = router.handle(msg)
    return Decision(rep.text, rep.intent, rep.source, rep.confidence,
                    rep.quote, rep.outcome, None, None)

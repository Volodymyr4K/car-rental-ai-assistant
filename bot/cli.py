"""Terminal chat simulator — talk to the bot without Telegram.

Runs the full router locally. Works at $0 with no keys (rules-only); if an
OpenRouter key is in .env, unmatched messages get an LLM answer too. Every turn is
logged to data/conversations.jsonl, so you can type `/stats` to see the live
effectiveness numbers (the measurement deliverable).

Run:  python3 -m bot.cli
"""

from __future__ import annotations

import uuid

from . import config
from .booking import Booking, BookingFlow
from .dispatch import dispatch
from .intent import Router
from .llm import answer as llm_answer
from .pricing import PriceBook
from .store import Turn, effectiveness, log_turn


def main() -> None:
    prices = PriceBook()
    router = Router(price_book=prices, llm=llm_answer if config.LLM_ENABLED else None)
    flow = BookingFlow(prices)
    state: Booking | None = None
    session = uuid.uuid4().hex[:8]
    mode = "rules+LLM" if config.LLM_ENABLED else "rules-only ($0, без LLM-ключа)"
    print(f"🚗 {config.COMPANY_NAME} асистент — режим: {mode}")
    print("Пиши повідомлення. /stats — метрики, /q — вийти.\n")

    while True:
        try:
            msg = input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg == "/q":
            break
        if msg == "/stats":
            print("📊", effectiveness(), "\n")
            continue

        d = dispatch(msg, state, router, flow)
        state = d.booking
        text, intent, source, conf, quote, outcome = (
            d.text, d.intent, d.source, d.confidence, d.quote, d.outcome)

        print(f"🤖 {text}\n   ·[{intent}/{source} conf={conf}]\n")
        log_turn(Turn(
            user_id="cli", session_id=session, message=msg,
            intent_rules=intent, intent_confidence=conf,
            answer_source=source, quote=quote, outcome=outcome,
        ))


if __name__ == "__main__":
    main()

"""Tests for the conversation log/metrics and the intent router."""

from bot.intent import Router
from bot.store import Turn, effectiveness, load_turns, log_turn, set_latest_feedback


def test_set_latest_feedback(tmp_path):
    log = tmp_path / "c.jsonl"
    log_turn(Turn("u", "s1", "привіт", outcome="answered"), log)
    log_turn(Turn("u", "s1", "бронь", outcome="lead_captured"), log)
    log_turn(Turn("u", "s2", "інше"), log)
    assert set_latest_feedback("s1", "booked", log) is True
    s1 = [t for t in load_turns(log) if t["session_id"] == "s1"]
    assert s1[-1]["manager_feedback"] == "booked"  # latest s1 turn labelled
    assert s1[0]["manager_feedback"] is None        # earlier turn untouched
    assert set_latest_feedback("missing", "booked", log) is False


def test_effectiveness_counts(tmp_path):
    log = tmp_path / "conv.jsonl"
    log_turn(Turn("u", "s", "застава?", "faq", 0.5, "rules", outcome="answered"), log)
    log_turn(Turn("u", "s", "щось дивне", None, 0.1, "llm", outcome="answered"), log)
    log_turn(Turn("u", "s", "скарга", None, 0.0, "human", outcome="escalated"), log)
    m = effectiveness(log)
    assert m["total"] == 3
    assert m["auto_resolved"] == 2          # rules + llm
    assert m["escalated_to_human"] == 1
    assert m["rules_share_pct"] == round(100 / 3, 1)


def test_router_price_quote():
    r = Router()
    rep = r.handle("Скільки коштує камрі гібрид на 5 днів?")
    assert rep.intent == "price" and rep.source == "rules"
    assert rep.quote is not None and rep.quote["days"] == 5


def test_router_faq():
    assert Router().handle("яка застава на бізнес?").intent == "faq"


def test_router_escalates_offtopic_without_llm():
    rep = Router().handle("яка столиця Бразилії")
    assert rep.intent == "escalate" and rep.source == "human"


def test_router_uses_llm_when_provided():
    r = Router(llm=lambda msg: "стаб-відповідь")
    rep = r.handle("розкажи анекдот про авто")
    assert rep.source == "llm" and rep.text == "стаб-відповідь"


def test_router_falls_back_to_human_when_llm_returns_none():
    # LLM timeout/failure (None) must NOT send an empty reply — hand to a human
    r = Router(llm=lambda msg: None)
    rep = r.handle("розкажи анекдот про авто")
    assert rep.source == "human" and rep.text and rep.outcome == "escalated"

"""Tests for the unified customer-message dispatch (and the 'хочу'-hijack fix)."""

from bot.booking import Booking, BookingFlow
from bot.dispatch import dispatch
from bot.intent import Router


def _rf():
    return Router(), BookingFlow()


def test_hochu_question_answers_faq_not_booking():
    # the bug: "хочу дізнатися про страховку" → booking flow ("яке авто?")
    r, f = _rf()
    d = dispatch("хочу дізнатися про страховку", None, r, f)
    assert d.intent == "faq" and "страхуванн" in d.text.lower()
    assert d.booking is None


def test_hochu_with_car_still_books():
    r, f = _rf()
    d = dispatch("хочу орендувати ауді", None, r, f)
    assert d.intent == "booking" and d.booking is not None


def test_vague_booking_word_books():
    r, f = _rf()
    d = dispatch("хочу авто", None, r, f)
    assert d.intent == "booking" and d.booking is not None


def test_plain_faq_unchanged():
    r, f = _rf()
    assert dispatch("яка застава на бізнес?", None, r, f).intent == "faq"


def test_price_quote_unchanged():
    r, f = _rf()
    d = dispatch("скільки коштує камрі гібрид на 5 днів", None, r, f)
    assert d.intent == "price" and d.quote is not None


def test_midbooking_continues_and_captures_lead():
    r, f = _rf()
    b = Booking()
    dispatch("хочу X5 2026", b, r, f)                 # car
    dispatch("на 5 днів", b, r, f)                    # days -> quote
    dispatch("у Києві", b, r, f)                      # city
    d = dispatch("Володимир, 0501234567", b, r, f)    # contact -> lead
    assert d.lead is not None and d.booking is None
    assert d.lead.phone == "0501234567"

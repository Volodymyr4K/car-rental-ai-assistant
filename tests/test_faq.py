"""Tests for rules-first FAQ retrieval (confident match vs escalate)."""

from bot.faq import FaqBook


def book():
    return FaqBook()


def test_confident_tag_match():
    b = book()
    assert b.answer("Яка застава на бізнес?").entry.id == "deposit"
    assert b.answer("чи приймаєте оплату карткою").entry.id == "payment"


def test_ukrainian_inflection_stemming():
    # 'кілометрів' must reach the 'mileage' topic despite the inflection
    m = book().match("скільки кілометрів на день")
    assert m.entry.id == "mileage"


def test_country_word_maps_to_abroad():
    assert book().answer("можна виїхати в польщу?").entry.id == "abroad"


def test_offtopic_does_not_answer():
    # off-topic must NOT produce a confident answer (escalate instead)
    assert book().answer("яка погода завтра") is None


def test_low_confidence_below_floor_escalates():
    b = book()
    m = b.match("привіт як справи")
    assert m is None or m.score < b.CONFIDENCE_FLOOR

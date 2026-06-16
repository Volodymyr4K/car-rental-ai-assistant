"""Tests for the slot-filling booking dialog."""

from bot.booking import Booking, BookingFlow, wants_booking


def test_wants_booking_trigger():
    assert wants_booking("хочу орендувати авто", has_car=False)
    assert not wants_booking("яка погода", has_car=False)


def test_full_booking_flow_collects_a_lead():
    flow = BookingFlow()
    b = Booking()

    s = flow.step(b, "Хочу БМВ на завтра")
    assert "модель" in s.text.lower()           # disambiguation asked
    assert b.date_from == "завтра"

    flow.step(b, "X5 2026")
    assert b.car == "BMW X5 2026"               # correct model picked

    s = flow.step(b, "на 5 днів")
    assert b.days == 5
    assert "€" in s.text                        # a price was quoted

    flow.step(b, "у Києві")
    assert b.city == "Київ"                     # inflected city recognised

    s = flow.step(b, "Володимир, 0501234567")
    assert b.done and s.outcome == "lead_captured"
    assert b.name == "Володимир" and b.phone == "0501234567"


def test_city_inflections():
    flow = BookingFlow()
    for text, expected in [("у Львові", "Львів"), ("в Одесі", "Одеса"),
                           ("Дніпро", "Дніпро")]:
        b = Booking()
        b.car, b.days = "Renault Logan II", 3   # skip ahead to city step
        flow.step(b, text)
        assert b.city == expected


def test_phone_extraction_with_name():
    flow = BookingFlow()
    b = Booking(car="Renault Logan II", days=3, city="Київ", quoted=True,
                awaiting="contact")
    flow.step(b, "Ігор +38 050 123 45 67")
    assert b.phone == "+380501234567"
    assert "Ігор" in (b.name or "")

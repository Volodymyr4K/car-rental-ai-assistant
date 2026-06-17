"""Regression tests for the Batch-1 audit fixes (demo-blockers)."""

from bot.booking import Booking, BookingFlow
from bot.intent import accident_reply, handoff_reasons, is_accident
from bot.pricing import PriceBook, deposit_str


def test_deposit_guard_never_shows_none():
    # incomplete data (Renault Duster has deposit=null) must never render "None€"
    assert deposit_str(None) == "уточнить менеджер"
    assert deposit_str(500) == "500€"
    q = PriceBook().quote("duster", 5)
    assert q is not None
    assert "None" not in deposit_str(q.deposit)


def test_class_name_lists_models_not_dead_end():
    flow = BookingFlow()
    for text, expect_one in [("хочу бізнес клас", "Infiniti Q50"),
                             ("позашляховик", "Renault Duster NEW"),
                             ("економ", "Renault Logan II")]:
        b = Booking()
        s = flow.step(b, text)
        assert b.awaiting == "choice" and b.options, f"no class options for {text!r}"
        assert expect_one in s.text or expect_one in b.options


def test_is_accident():
    assert is_accident("я потрапив у ДТП на вашому авто")
    assert is_accident("врізався в стовп")
    assert not is_accident("хочу орендувати авто")


def test_handoff_reasons():
    assert "клієнт просить менеджера" in handoff_reasons("хочу менеджера")
    assert "можлива скарга / негатив" in handoff_reasons("це обман, поверніть гроші")
    assert handoff_reasons("яка застава?") == []


def test_accident_reply_contains_real_phone():
    r = accident_reply("+38 000 000 0000")
    assert "+38 000 000 0000" in r and "112" in r


def test_booking_escape_hatch():
    flow = BookingFlow()
    b = Booking(car="Renault Logan II", days=3, city="Київ", quoted=True,
                awaiting="contact")
    s = flow.step(b, "дякую, я подумаю")
    assert b.done and s.outcome == "cancelled"


def test_normal_booking_inputs_do_not_cancel():
    flow = BookingFlow()
    # incl. class-switch phrases that must NOT be read as a cancel
    for ok in ["Володимир", "на 5 днів", "у Києві", "BMW X5 2026", "0501234567",
               "економ не цікавить, хочу бізнес", "забудь про той, покажи інший"]:
        b = Booking(car="Renault Logan II", days=3, awaiting="contact")
        s = flow.step(b, ok)
        assert s.outcome != "cancelled", f"{ok!r} wrongly cancelled booking"

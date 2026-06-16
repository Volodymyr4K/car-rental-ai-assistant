"""Customer inline-keyboard buttons reuse the SAME dispatch path as typed text.

Data-driven: derived from whatever fleet/FAQ the sample data provides, so they
hold regardless of how many classes/models the catalog has.
"""

import bot.bot as b
from bot.dispatch import dispatch
from bot.intent import handoff_reasons, is_accident


def test_class_buttons_all_start_booking():
    # every class on the keyboard must synthesize a message that starts booking —
    # otherwise the button is dead (sends the user to the LLM/escalation instead).
    for cls in b._class_names():
        msg = b.customer_cb_message(f"cust:class:{cls}")
        assert msg == f"орендувати {cls}"
        d = dispatch(msg, None, b.router, b.flow)
        assert d.intent == "booking", f"class button {cls!r} did not start booking"


def test_manager_button_triggers_handoff_not_accident():
    msg = b.customer_cb_message("cust:manager")
    assert handoff_reasons(msg)        # must hand off to a human
    assert not is_accident(msg)        # must NOT trip the accident path


def test_pure_ui_buttons_send_no_message():
    assert b.customer_cb_message("cust:rent") is None
    assert b.customer_cb_message("cust:menu") is None


def test_handoff_catches_instrumental_case():
    # regression: 'менеджером' (instrumental) was missing from HUMAN_WORDS
    assert handoff_reasons("звʼяжіть мене з менеджером")
    assert handoff_reasons("хочу поговорити з оператором")


def test_model_buttons_offered_after_class():
    # picking a class must surface its models as button options (no dead-end text)
    first_class = b._class_names()[0]
    d = dispatch(f"орендувати {first_class}", None, b.router, b.flow)
    assert d.booking and d.booking.options
    kb = b._model_kb(d.booking.options)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any(opt in labels for opt in d.booking.options)


def test_faq_buttons_all_resolve_with_label():
    # no dead FAQ buttons, and each has a short label (so it won't truncate)
    for e in b.router.faq.entries:
        assert b._faq_entry(e.id) is not None and b._faq_entry(e.id).answer
        assert e.id in b._FAQ_LABELS

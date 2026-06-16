"""Tests for operator-takeover state (modes, topics, cards, greeting).

Isolates the JSON files to a temp dir so tests never touch real runtime state.
"""

import pytest

from bot import ops_state


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_state, "_MODES", tmp_path / "modes.json")
    monkeypatch.setattr(ops_state, "_TOPICS", tmp_path / "topics.json")
    monkeypatch.setattr(ops_state, "_CARDS", tmp_path / "cards.json")
    ops_state._greeted.clear()
    ops_state._relay.clear()
    yield


def test_mode_default_and_toggle():
    assert ops_state.get_mode(1) == "auto"
    assert not ops_state.is_human(1)
    assert ops_state.toggle(1) == "human" and ops_state.is_human(1)
    assert ops_state.toggle(1) == "auto"


def test_resume_clears_greeting():
    ops_state.set_mode(2, "human")
    assert ops_state.needs_greeting(2) is True    # first reply greets
    assert ops_state.needs_greeting(2) is False   # subsequent do not
    ops_state.set_mode(2, "auto")                 # takeover ended
    assert ops_state.needs_greeting(2) is True    # greets again next time


def test_suppress_greeting():
    # accident/complaint takeover must skip the cheerful greeting
    ops_state.suppress_greeting(5)
    assert ops_state.needs_greeting(5) is False


def test_topic_mapping_roundtrip():
    ops_state.set_topic(100, 555)
    assert ops_state.get_topic(100) == 555
    assert ops_state.customer_by_thread(555) == 100
    assert ops_state.customer_by_thread(999) is None


def test_card_update_and_notes():
    ops_state.update_card(7, status="ЛІД", phone="050")
    ops_state.add_card_note(7, "VIP")
    ops_state.add_card_note(7, "дзвонити після 18")
    card = ops_state.get_card(7)
    assert card["status"] == "ЛІД" and card["phone"] == "050"
    assert card["notes"] == ["VIP", "дзвонити після 18"]


def test_relay_map():
    ops_state.link(321, 7)
    assert ops_state.resolve(321) == 7
    assert ops_state.resolve(404) is None

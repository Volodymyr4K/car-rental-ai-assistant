"""Operator-takeover state (pure, testable — no Telegram here).

Two things to track:
  1. mode per customer conversation: 'auto' (bot answers) or 'human' (operator
     took over, bot stays silent). Persisted to data/modes.json so a restart does
     not silently put the bot back in charge of a chat an operator was handling.
  2. relay map: which operators-group message corresponds to which customer chat,
     so an operator's reply is routed back to the right customer. In-memory only
     (rebuilt as new messages arrive) — losing it on restart just means older
     forwarded messages stop being reply-able, which is acceptable.
"""

from __future__ import annotations

import json
from pathlib import Path

_MODES = Path(__file__).resolve().parent.parent / "data" / "modes.json"


def _load() -> dict:
    if _MODES.exists():
        try:
            return json.loads(_MODES.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save(d: dict) -> None:
    _MODES.parent.mkdir(parents=True, exist_ok=True)
    _MODES.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def get_mode(customer_id: int) -> str:
    return _load().get(str(customer_id), "auto")


def is_human(customer_id: int) -> bool:
    return get_mode(customer_id) == "human"


def set_mode(customer_id: int, mode: str) -> None:
    assert mode in ("auto", "human")
    d = _load()
    d[str(customer_id)] = mode
    _save(d)
    if mode == "auto":
        # takeover ended → next time a manager joins, greet again
        _greeted.discard(customer_id)


# which customers have already gotten the "a human joined" greeting this takeover
_greeted: set[int] = set()


def needs_greeting(customer_id: int) -> bool:
    """True once per takeover session — for the manager's first reply."""
    if customer_id in _greeted:
        return False
    _greeted.add(customer_id)
    return True


def suppress_greeting(customer_id: int) -> None:
    """Skip the cheerful 'Вітаю!' for accident/complaint takeovers (tonally wrong);
    the manager's first reply just gets the normal signature."""
    _greeted.add(customer_id)


# customer cards: pinned per-topic summary the bot maintains (auto-filled + notes)
_CARDS = Path(__file__).resolve().parent.parent / "data" / "cards.json"


def _load_cards() -> dict:
    if _CARDS.exists():
        try:
            return json.loads(_CARDS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cards(d: dict) -> None:
    _CARDS.parent.mkdir(parents=True, exist_ok=True)
    _CARDS.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def get_card(customer_id: int) -> dict:
    return _load_cards().get(str(customer_id), {})


def update_card(customer_id: int, **fields) -> dict:
    d = _load_cards()
    card = d.get(str(customer_id), {})
    card.update({k: v for k, v in fields.items() if v is not None})
    d[str(customer_id)] = card
    _save_cards(d)
    return card


def add_card_note(customer_id: int, note: str) -> dict:
    d = _load_cards()
    card = d.get(str(customer_id), {})
    card.setdefault("notes", []).append(note)
    d[str(customer_id)] = card
    _save_cards(d)
    return card


def toggle(customer_id: int) -> str:
    new = "auto" if is_human(customer_id) else "human"
    set_mode(customer_id, new)
    return new


# relay map: operators-group message_id -> customer chat_id (in-memory fallback
# for non-forum groups, where we route an operator reply by reply-to message)
_relay: dict[int, int] = {}


def link(group_message_id: int, customer_id: int) -> None:
    _relay[group_message_id] = customer_id


def resolve(group_message_id: int) -> int | None:
    return _relay.get(group_message_id)


# forum topics: customer chat_id <-> message_thread_id (persisted). One topic per
# customer = one clean "tab" in the operators group; routing is by thread.
_TOPICS = Path(__file__).resolve().parent.parent / "data" / "topics.json"


def _load_topics() -> dict:
    if _TOPICS.exists():
        try:
            return json.loads(_TOPICS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def get_topic(customer_id: int) -> int | None:
    t = _load_topics().get(str(customer_id))
    return int(t) if t is not None else None


def set_topic(customer_id: int, thread_id: int) -> None:
    d = _load_topics()
    d[str(customer_id)] = thread_id
    _TOPICS.parent.mkdir(parents=True, exist_ok=True)
    _TOPICS.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def customer_by_thread(thread_id: int) -> int | None:
    for cid, tid in _load_topics().items():
        if int(tid) == thread_id:
            return int(cid)
    return None


if __name__ == "__main__":
    # quick self-check
    cid = 999
    set_mode(cid, "auto")
    assert get_mode(cid) == "auto" and not is_human(cid)
    assert toggle(cid) == "human" and is_human(cid)
    assert toggle(cid) == "auto"
    link(111, cid)
    assert resolve(111) == cid and resolve(222) is None
    set_mode(cid, "auto")  # leave clean
    print("ops_state OK")

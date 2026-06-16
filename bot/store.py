"""ML-ready conversation logging.

Every bot turn is appended to data/conversations.jsonl as one JSON object. This is
NOT just analytics — it is the future training dataset (see docs/plan.md §4). The
schema is fixed from turn #1 so that, months later, ML on lead-scoring / intent has
clean labeled history to learn from. The most valuable field is `manager_feedback`:
it is the ground-truth label (did the lead book? was the answer right?).

No DB, no dependencies — append-only JSONL is enough at this scale and trivially
loadable into pandas later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOG = Path(__file__).resolve().parent.parent / "data" / "conversations.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Turn:
    """One message → response cycle. Fields mirror docs/plan.md §4."""
    user_id: str
    session_id: str
    message: str                              # raw client text
    intent_rules: Optional[str] = None        # what the rules decided (faq/price/booking/none)
    intent_confidence: Optional[float] = None  # rules confidence (for threshold tuning)
    answer_source: Optional[str] = None       # rules | llm | human
    quote: Optional[dict] = None              # {car, days, total, deposit} if a price was given
    outcome: Optional[str] = None             # lead_captured | abandoned | escalated | answered
    manager_feedback: Optional[str] = None    # GROUND TRUTH, filled later: booked | lost | wrong_answer ...
    ts: str = field(default_factory=_now)


def log_turn(turn: Turn, path: Path = _LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")


def load_turns(path: Path = _LOG) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def set_latest_feedback(session_id: str, feedback: str, path: Path = _LOG) -> bool:
    """Write the manager's ground-truth label onto the latest turn of a session.
    This is the ML label (booked/lost/wrong_answer) plan.md §4 calls the most
    valuable field. Returns False if no turn for that session exists."""
    turns = load_turns(path)
    for i in range(len(turns) - 1, -1, -1):
        if turns[i].get("session_id") == session_id:
            turns[i]["manager_feedback"] = feedback
            with path.open("w", encoding="utf-8") as f:
                for t in turns:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
            return True
    return False


def effectiveness(path: Path = _LOG) -> dict:
    """The measurement deliverable: how much the bot closes on its own, and the
    rules-vs-LLM split. This is what proves 'AI без хайпу, виміряно' to the owner."""
    turns = load_turns(path)
    total = len(turns)
    if total == 0:
        return {"total": 0}
    by_source: dict[str, int] = {}
    for t in turns:
        src = t.get("answer_source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    auto = by_source.get("rules", 0) + by_source.get("llm", 0)
    return {
        "total": total,
        "by_source": by_source,
        "auto_resolved": auto,
        "auto_resolved_pct": round(100 * auto / total, 1),
        "escalated_to_human": by_source.get("human", 0),
        "rules_share_pct": round(100 * by_source.get("rules", 0) / total, 1),
        "llm_share_pct": round(100 * by_source.get("llm", 0) / total, 1),
    }


if __name__ == "__main__":
    # demo: log a few synthetic turns to a scratch file and print metrics
    scratch = Path("/tmp/conv_demo.jsonl")
    scratch.unlink(missing_ok=True)
    log_turn(Turn("u1", "s1", "яка застава?", "faq", 0.5, "rules", outcome="answered"), scratch)
    log_turn(Turn("u1", "s1", "порахуй камрі на 5 днів", "price", 0.8, "rules",
                   quote={"car": "Toyota Camry HYBRID (XV70)", "days": 5, "total": 305, "deposit": 500},
                   outcome="answered"), scratch)
    log_turn(Turn("u2", "s2", "а можна з собакою і дитячим кріслом одночасно?", None, 0.2, "llm",
                   outcome="answered"), scratch)
    log_turn(Turn("u3", "s3", "хочу поскаржитись на менеджера", None, 0.1, "human",
                   outcome="escalated"), scratch)
    print(json.dumps(effectiveness(scratch), ensure_ascii=False, indent=2))

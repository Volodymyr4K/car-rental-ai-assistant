"""Tiny .env loader + settings. No external dependency (python-dotenv not required).

Reads KEY=VALUE lines from a .env file in the project root into os.environ, then
exposes the settings the bot needs. Missing secrets are fine — the bot degrades
gracefully (rules-only, no LLM) so it still runs at $0 for local testing.
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENV = _ROOT / ".env"


def load_env(path: Path = _ENV) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        # don't override anything already set in the real environment
        os.environ.setdefault(key, val)


load_env()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "AutoRent")  # brand shown to customers
OPERATORS_CHAT_ID = os.environ.get("OPERATORS_CHAT_ID", "")  # group where managers watch + take over
# managers to @mention on alerts (user IDs, comma-separated). Get an ID via /whoami.
MANAGER_IDS = [int(x) for x in os.environ.get("MANAGER_IDS", "").replace(" ", "").split(",")
               if x.lstrip("-").isdigit()]
# explicit customer-facing names per manager: "id=Name,id=Name". NEVER auto-pulled
# from Telegram profiles (those are uncontrolled and may be unprofessional).
# real company phone for safety/emergency replies (never let the LLM invent one)
COMPANY_PHONE = os.environ.get("COMPANY_PHONE", "+38 0XX XXX XXXX")
MANAGER_NAMES: dict[int, str] = {}
for _pair in os.environ.get("MANAGER_NAMES", "").split(","):
    if "=" in _pair:
        _k, _v = _pair.split("=", 1)
        _k, _v = _k.strip(), _v.strip()
        if _k.lstrip("-").isdigit() and _v:
            MANAGER_NAMES[int(_k)] = _v
# LLM is OpenAI-compatible, so the same code talks to OpenRouter OR a local runtime
# (Ollama / LM Studio / llama.cpp) — only the base URL + model name change.
_DEFAULT_BASE = "https://openrouter.ai/api/v1"
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", _DEFAULT_BASE).rstrip("/")
# LLM_API_KEY is preferred; OPENROUTER_API_KEY kept as a backwards-compatible alias
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_KEY = LLM_API_KEY  # legacy alias
# free model by default → $0 for the test; override in .env for a stronger one
LLM_MODEL = os.environ.get("LLM_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
# enabled if we have a key OR a custom (e.g. local) base URL is configured
LLM_ENABLED = bool(LLM_API_KEY) or LLM_BASE_URL != _DEFAULT_BASE

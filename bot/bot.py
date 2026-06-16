"""Telegram bot — the deliverable the owner actually tests (just messages it).

Thin wrapper over the router: receive message → route → reply → log. The brains
(pricing, FAQ, intent, LLM) live in the other modules; this file only wires them
to Telegram. It also runs the operator console (per-customer topics, takeover,
signed replies, customer cards, mute-piercing alerts to the managers' group).

Run:
    pip install -r requirements.txt
    cp .env.example .env   # then fill TELEGRAM_BOT_TOKEN (+ optional LLM_API_KEY)
    python3 -m bot.bot
"""

from __future__ import annotations

import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import config, ops_state
from .booking import Booking, BookingFlow
from .dispatch import dispatch
from .intent import Router, accident_reply, handoff_reasons, is_accident
from .llm import answer as llm_answer
from .pricing import PriceBook
from .store import Turn, effectiveness, log_turn, set_latest_feedback

# operator shortcuts that label the lead (ML ground truth) in addition to a note
_FEEDBACK = {"booked": "booked", "забронював": "booked", "купив": "booked",
             "lost": "lost", "злився": "lost", "відмова": "lost", "відмовився": "lost",
             "wrong": "wrong_answer", "невірно": "wrong_answer", "неправильно": "wrong_answer", "помилка": "wrong_answer"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

_prices = PriceBook()
router = Router(price_book=_prices, llm=llm_answer if config.LLM_ENABLED else None)
flow = BookingFlow(_prices)


def _ops_id() -> int | None:
    return int(config.OPERATORS_CHAT_ID) if config.OPERATORS_CHAT_ID else None


def _kb(customer_id: int) -> InlineKeyboardMarkup:
    """Takeover/resume button reflecting the conversation's current mode."""
    if ops_state.is_human(customer_id):
        label, act = "▶️ Повернути боту", "auto"
    else:
        label, act = "🛑 Перехопити", "human"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"mode:{act}:{customer_id}")]]
    )


def _render_card(card: dict) -> str:
    lines = ["📌 <b>Картка клієнта</b>"]
    if card.get("handle"):
        lines.append(f"👤 {html.escape(str(card['handle']))}")
    lines.append(f"🏷 Статус: {html.escape(str(card.get('status', 'новий')))}")
    if card.get("interest"):
        lines.append(f"🚗 Інтерес: {html.escape(str(card['interest']))}")
    if card.get("phone"):
        lines.append(f"📞 {html.escape(str(card['phone']))}")
    notes = card.get("notes", [])
    if notes:
        lines.append("📝 Нотатки:")
        lines += [f"• {html.escape(str(n))}" for n in notes]
    lines.append("\n<i>Нотатка для команди (не клієнту): // текст</i>")
    return "\n".join(lines)


async def _ensure_card(context: ContextTypes.DEFAULT_TYPE,
                       customer_id: int, handle: str, thread_id: int | None) -> None:
    """Create + pin the customer card at the top of the topic, once."""
    card = ops_state.get_card(customer_id)
    if card.get("pin"):
        return
    card = ops_state.update_card(customer_id, handle=handle,
                                 status=card.get("status", "новий"))
    try:
        m = await context.bot.send_message(
            _ops_id(), _render_card(card), message_thread_id=thread_id,
            parse_mode="HTML", disable_notification=True)
        await context.bot.pin_chat_message(_ops_id(), m.message_id,
                                            disable_notification=True)
        ops_state.update_card(customer_id, pin=m.message_id)
    except Exception as e:
        log.warning("card create/pin failed: %s", e)


async def _refresh_card(context: ContextTypes.DEFAULT_TYPE, customer_id: int) -> None:
    card = ops_state.get_card(customer_id)
    pin = card.get("pin")
    if not pin:
        return
    try:
        await context.bot.edit_message_text(
            _render_card(card), chat_id=_ops_id(), message_id=pin, parse_mode="HTML")
    except Exception as e:  # ignore "message not modified" and transient errors
        log.debug("card refresh: %s", e)


async def _ensure_topic(context: ContextTypes.DEFAULT_TYPE,
                        customer_id: int, title: str) -> int | None:
    """One forum topic per customer = one clean tab. Returns its thread id, or
    None if the group is not a forum (then we fall back to flat messages)."""
    tid = ops_state.get_topic(customer_id)
    if tid is not None:
        return tid
    try:
        t = await context.bot.create_forum_topic(_ops_id(), name=title[:120])
        ops_state.set_topic(customer_id, t.message_thread_id)
        return t.message_thread_id
    except Exception as e:  # forum disabled or no Manage-Topics permission
        log.warning("create_forum_topic failed (Topics off?): %s", e)
        return None


async def _to_ops(context: ContextTypes.DEFAULT_TYPE, text: str,
                  customer_id: int | None = None,
                  thread_id: int | None = None) -> None:
    """Mirror a message into the operators group (into the customer's topic if we
    have one). With customer_id, attach the takeover button + keep a reply-relay
    fallback for non-forum groups."""
    ops = _ops_id()
    if not ops:
        return
    kb = _kb(customer_id) if customer_id is not None else None
    try:
        # routine mirroring is SILENT — only real alerts (see _alert) make a sound
        m = await context.bot.send_message(
            ops, text, reply_markup=kb, message_thread_id=thread_id,
            disable_notification=True)
        if customer_id is not None:
            ops_state.link(m.message_id, customer_id)
    except Exception as e:  # never let mirroring break the customer chat
        log.warning("ops mirror failed: %s", e)


# --- alerts: loud, mute-piercing pings only when a human is really needed ---
def _outcome_reason(outcome: str) -> str | None:
    """Outcome-based alerts that fire after a normal auto reply."""
    if outcome == "lead_captured":
        return "зібрано лід — підтвердити наявність"
    if outcome == "escalated":
        return "бот не зміг — потрібна людина"
    return None


_mention_cache: dict[int, str] = {}


async def _mention(context: ContextTypes.DEFAULT_TYPE, uid: int) -> str:
    """Render a manager mention: @username if they have one, otherwise their real
    name as a clickable text_mention. Both notify and pierce a muted topic."""
    if uid in _mention_cache:
        return _mention_cache[uid]
    rendered = f'<a href="tg://user?id={uid}">менеджер</a>'  # fallback
    try:
        u = await context.bot.get_chat(uid)
        if u.username:
            rendered = f"@{u.username}"
        elif u.first_name:
            rendered = f'<a href="tg://user?id={uid}">{html.escape(u.first_name)}</a>'
    except Exception as e:
        log.warning("get_chat(%s) failed: %s", uid, e)
    _mention_cache[uid] = rendered
    return rendered


async def _alert(context: ContextTypes.DEFAULT_TYPE, reason: str,
                 customer_msg: str, thread_id: int | None) -> None:
    ops = _ops_id()
    if not ops:
        return
    mentions = " ".join([await _mention(context, i) for i in config.MANAGER_IDS])
    text = (f"🔔 <b>Потрібен менеджер:</b> {reason}\n"
            f"💬 «{html.escape(customer_msg)}»")
    if mentions:
        text += f"\n👉 {mentions}"
    try:
        await context.bot.send_message(ops, text, message_thread_id=thread_id,
                                       parse_mode="HTML")  # loud (notifies)
    except Exception as e:
        log.warning("alert failed: %s", e)

_WELCOME = (
    f"Вітаю! Я асистент {config.COMPANY_NAME} 🚗\n"
    "Питайте про ціну, умови, документи чи бронювання — відповім миттєво, "
    "24/7. Напр.: «скільки коштує Camry на 5 днів?»"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_WELCOME)


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the current chat's ID — use it in a group to get OPERATORS_CHAT_ID."""
    chat = update.effective_chat
    await update.message.reply_text(
        f"Chat ID: `{chat.id}`\nТип: {chat.type}\n"
        f"Встав це значення у .env як OPERATORS_CHAT_ID.",
        parse_mode="Markdown",
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the sender's user ID — managers run this to fill MANAGER_IDS."""
    u = update.effective_user
    await update.message.reply_text(
        f"Ваш user ID: `{u.id}`\nUsername: @{u.username or '—'}\n"
        f"Додай цей ID у MANAGER_IDS (.env), щоб отримувати тривоги.",
        parse_mode="Markdown",
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live effectiveness numbers — handy to show the owner during the demo."""
    if not _is_manager(update.effective_user.id if update.effective_user else None):
        return  # business metrics are not for random users
    m = effectiveness()
    if not m.get("total"):
        await update.message.reply_text("Поки немає даних.")
        return
    await update.message.reply_text(
        f"📊 Усього звернень: {m['total']}\n"
        f"Авто-закрито ботом: {m['auto_resolved_pct']}%\n"
        f"Правила: {m['rules_share_pct']}% · LLM: {m['llm_share_pct']}%\n"
        f"Ескалація людині: {m['escalated_to_human']}"
    )


def _handle(u) -> str:
    return f"@{u.username}" if u.username else (u.full_name or f"id{u.id}")


def _is_manager(uid: int | None) -> bool:
    """Authorized operator. If MANAGER_IDS is unset (demo), allow; otherwise enforce."""
    return (not config.MANAGER_IDS) or (uid in config.MANAGER_IDS)


async def on_ops_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A message inside the operators group. If it replies to a forwarded customer
    message, relay it to that customer and mark the chat human (manual takeover)."""
    m = update.message
    if not m or not m.text:
        return
    if not _is_manager(update.effective_user.id if update.effective_user else None):
        return  # only configured managers may relay / take over
    # route by topic (forum) first, then by reply-to (flat-group fallback)
    customer_id = None
    if m.message_thread_id is not None:
        customer_id = ops_state.customer_by_thread(m.message_thread_id)
    if customer_id is None and m.reply_to_message:
        customer_id = ops_state.resolve(m.reply_to_message.message_id)
    if customer_id is None:
        return  # not a customer conversation (e.g. General topic chatter)

    # internal note: '// текст' stays in the team's card, NOT relayed to the customer.
    # If the note is a label word (//booked, //lost, //wrong) it also writes the
    # ML ground-truth feedback onto this conversation's latest logged turn.
    if m.text.startswith("//"):
        note = m.text[2:].strip()
        if note:
            label = _FEEDBACK.get(note.lower())
            if label:
                set_latest_feedback(str(customer_id), label)
            ops_state.add_card_note(customer_id, note)
            await _refresh_card(context, customer_id)
            try:
                await m.set_reaction("👌")  # confirm note saved (card also updates)
            except Exception:
                pass
        return

    # sign the reply so the customer sees a human now. Name comes from explicit
    # config only — never from the operator's (uncontrolled) Telegram profile.
    op = update.effective_user
    name = config.MANAGER_NAMES.get(op.id)
    sig = f"Менеджер {name}" if name else "Менеджер"      # capital М, name intact
    who = f"менеджер {name}" if name else "менеджер"      # lowercase, mid-sentence
    if ops_state.needs_greeting(customer_id):
        out = f"Вітаю! 👋 З вами {who}, далі допоможу особисто.\n\n{m.text}"
    else:
        out = f"👤 {sig}:\n{m.text}"
    try:
        await context.bot.send_message(customer_id, out)
    except Exception as e:
        await m.reply_text(f"⚠️ Не вдалося надіслати клієнту: {e}")
        return
    ops_state.set_mode(customer_id, "human")


async def on_mode_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Takeover / resume button under a forwarded message."""
    q = update.callback_query
    if not _is_manager(q.from_user.id if q.from_user else None):
        await q.answer("Лише для менеджерів", show_alert=True)
        return
    await q.answer()
    try:
        _, mode, cid_s = q.data.split(":")
        customer_id = int(cid_s)
    except (ValueError, AttributeError):
        return
    ops_state.set_mode(customer_id, mode)
    try:
        await q.edit_message_reply_markup(reply_markup=_kb(customer_id))
    except Exception:
        pass
    note = "🛑 Перехоплено — бот мовчить" if mode == "human" else "▶️ Повернуто боту"
    # send the status into the customer's topic, not the group's General feed
    thread_id = q.message.message_thread_id if q.message else None
    await context.bot.send_message(_ops_id(), note, message_thread_id=thread_id)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # operators group goes to its own handler
    if _ops_id() and update.effective_chat.id == _ops_id():
        return await on_ops_message(update, context)

    msg = update.message.text or ""
    user = update.effective_user
    cid = update.effective_chat.id

    # one topic (tab) per customer, with a pinned customer card on top
    thread_id = await _ensure_topic(context, cid, _handle(user))
    await _ensure_card(context, cid, _handle(user), thread_id)
    await _to_ops(context, f"👤 {_handle(user)}:\n{msg}",
                  customer_id=cid, thread_id=thread_id)

    # if an operator has taken over, the bot stays silent here
    if ops_state.is_human(cid):
        log_turn(Turn(user_id=str(user.id), session_id=str(cid), message=msg,
                      intent_rules="human_mode", answer_source="human",
                      outcome="human_handled"))
        return

    # TOP PRIORITY: accident → fixed safety reply with the REAL phone (never the
    # LLM, which invents numbers), then alert managers loudly.
    if is_accident(msg):
        context.user_data.pop("booking", None)  # drop any half-finished booking
        await update.message.reply_text(accident_reply(config.COMPANY_PHONE))
        await _alert(context, "🚨 ДТП / аварія — терміново", msg, thread_id)
        ops_state.set_mode(cid, "human")
        ops_state.suppress_greeting(cid)  # no cheerful 'Вітаю!' after an accident
        log_turn(Turn(user_id=str(user.id), session_id=str(cid), message=msg,
                      intent_rules="accident", answer_source="rules-safety",
                      outcome="escalated"))
        return

    # explicit request for a human / complaint → hold + alert, never run sales
    handoff = handoff_reasons(msg)
    if handoff:
        context.user_data.pop("booking", None)  # abandon any half-finished booking
        await update.message.reply_text(
            "Передаю вашу розмову менеджеру — він скоро відповість 🙏")
        await _alert(context, "; ".join(handoff), msg, thread_id)
        ops_state.set_mode(cid, "human")  # bot steps back until a manager resumes
        ops_state.suppress_greeting(cid)  # complaint takeover → skip the cheerful greeting
        log_turn(Turn(user_id=str(user.id), session_id=str(cid), message=msg,
                      intent_rules="handoff", answer_source="human",
                      outcome="escalated"))
        return

    # --- auto mode: unified decision (run in executor so a blocking LLM call
    #     never freezes the event loop for other customers/operators) ---
    state: Booking = context.user_data.get("booking")
    loop = asyncio.get_running_loop()
    d = await loop.run_in_executor(None, dispatch, msg, state, router, flow)
    if d.booking is not None:
        context.user_data["booking"] = d.booking
    else:
        context.user_data.pop("booking", None)
    if d.lead is not None:
        bk = d.lead
        ops_state.update_card(
            cid, status="ЛІД", phone=bk.phone,
            interest=f"{bk.car}, {bk.days} дн, {bk.city or '—'}")
    text, intent, source, conf, quote, outcome = (
        d.text, d.intent, d.source, d.confidence, d.quote, d.outcome)

    await update.message.reply_text(text)
    log_turn(Turn(
        user_id=str(user.id), session_id=str(cid), message=msg,
        intent_rules=intent, intent_confidence=conf,
        answer_source=source, quote=quote, outcome=outcome,
    ))
    # mirror the bot's answer into the customer's topic for full visibility
    prefix = "🔥 ЛІД · " if outcome == "lead_captured" else "🤖 "
    await _to_ops(context, f"{prefix}{text}", thread_id=thread_id)

    # keep the pinned card in sync after a completed lead
    if outcome == "lead_captured":
        await _refresh_card(context, cid)

    # loud, mute-piercing ping on outcome-based events (lead captured / bot failed)
    reason = _outcome_reason(outcome)
    if reason:
        await _alert(context, reason, msg, thread_id)
    # rules-only genuine "can't help" → hand to human so the next message doesn't
    # fire another identical alert (no escalation spam). With LLM on, an escalation
    # means a TRANSIENT LLM failure (e.g. 429) — don't go silent, allow a retry.
    if outcome == "escalated" and not config.LLM_ENABLED:
        ops_state.set_mode(cid, "human")


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Немає TELEGRAM_BOT_TOKEN. Створи .env з .env.example.")
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", chat_id))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(on_mode_button, pattern=r"^mode:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    mode = "rules+LLM" if config.LLM_ENABLED else "rules-only"
    log.info("Bot running (%s). Ctrl+C to stop.", mode)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

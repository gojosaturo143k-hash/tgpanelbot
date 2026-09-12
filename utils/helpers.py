"""
Shared request helpers for the API routes.

NOTE: Telegram group/supergroup ids are NEGATIVE numbers like
-1001234567890, so Flask's built-in <int:...> converter cannot be used
(it does not match the minus sign). Routes accept a string and parse it
here instead.
"""

import logging

from flask import jsonify, request

from database.db import db_session
from database.models import TgChat
from services.telegram_service import TelegramAPIError

log = logging.getLogger(__name__)


def parse_chat_id(raw):
    """Parse a Telegram chat id from the URL path. Returns int or None."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def get_int_arg(name, default, minimum=None, maximum=None):
    """Read an integer query-string argument with clamping."""
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def get_search_query():
    return (request.args.get("q") or "").strip()


def error(status, message, **extra):
    """Build a consistent JSON error response."""
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def load_chat(chat_id):
    """Fetch a TgChat row by its real Telegram chat id (or None)."""
    with db_session() as session:
        chat = (
            session.query(TgChat)
            .filter(TgChat.telegram_chat_id == int(chat_id))
            .one_or_none()
        )
        return chat.to_dict() if chat else None


def mark_chat_inactive(chat_id):
    """Flag a chat as inactive (bot was removed/blocked)."""
    try:
        with db_session() as session:
            chat = (
                session.query(TgChat)
                .filter(TgChat.telegram_chat_id == int(chat_id))
                .one_or_none()
            )
            if chat:
                chat.active = False
    except Exception:  # noqa: BLE001 - best-effort bookkeeping
        log.exception("Failed to mark chat inactive (chat_id=%s)", chat_id)


def telegram_error_response(exc):
    """
    Map a TelegramAPIError to a clean JSON API response.

    No stack traces, no secrets - just a safe message and a status code.
    """
    status = exc.status_code or 502

    if status == 429:
        log.warning("Reply blocked by Telegram rate limit (retry in %ss)", exc.retry_after)
        return error(
            429,
            "Telegram rate limit reached. Try again later.",
            retry_after=exc.retry_after,
        )
    if status == 400:
        return error(400, "Telegram rejected the request: %s" % exc.message)
    if status == 403:
        return error(
            403,
            "Telegram refused: the bot was blocked by the user or removed "
            "from the chat (%s)" % exc.message,
        )
    if status in (401, 404):
        return error(502, "Telegram configuration problem: %s" % exc.message)

    log.error("Telegram send failed: HTTP %s - %s", status, exc.message)
    return error(502, "Failed to reach Telegram. Please try again.")

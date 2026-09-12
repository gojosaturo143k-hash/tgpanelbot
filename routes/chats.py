"""
Web Panel API - generic chat operations.

    POST /api/chats/<chat_id>/read
        -> reset that conversation's unread_count to 0.
           Works for both groups and private DMs, keyed by the exact
           Telegram chat id so the correct chat is always updated.
"""

import logging

from flask import Blueprint, jsonify, request

from database.db import db_session
from database.models import TgChat, TgMessage, serialize_messages
from utils.auth import require_panel_auth
from utils.helpers import error, get_int_arg, parse_chat_id

log = logging.getLogger(__name__)

bp = Blueprint("chats", __name__, url_prefix="/api/chats")


@bp.post("/<chat_id_raw>/read")
@require_panel_auth
def mark_as_read(chat_id_raw):
    chat_id = parse_chat_id(chat_id_raw)
    if chat_id is None:
        return error(400, "Invalid chat id")

    with db_session() as session:
        chat = (
            session.query(TgChat)
            .filter(TgChat.telegram_chat_id == chat_id)
            .one_or_none()
        )
        if chat is None:
            return error(404, "Chat not found for this chat id")

        chat.unread_count = 0
        chat_data = chat.to_dict()

    log.info("Chat marked as read (chat_id=%s)", chat_id)
    return jsonify({"ok": True, "chat": chat_data, "unread_count": 0})


@bp.get("/<chat_id_raw>/messages/<int:telegram_message_id>")
@require_panel_auth
def get_single_message(chat_id_raw, telegram_message_id):
    """
    Fetch ONE message of a chat by its real Telegram message id.

    Used for "click the reply preview to jump to the original" when the
    original is not in the currently loaded window. Returns 404 with a
    clear reason when the message is unknown (e.g. it predates the bot or
    was deleted), so the panel can show a graceful state.
    """
    chat_id = parse_chat_id(chat_id_raw)
    if chat_id is None:
        return error(400, "Invalid chat id")

    with db_session() as session:
        message = (
            session.query(TgMessage)
            .filter(
                TgMessage.telegram_chat_id == chat_id,
                TgMessage.telegram_message_id == telegram_message_id,
            )
            .one_or_none()
        )
        if message is None:
            return error(
                404,
                "Original message is not available in this conversation",
                reason="not_stored",
            )

        payload = serialize_messages(session, [message])[0]

        # How many newer messages exist, so the panel knows how far back
        # it must page to reach this message.
        newer_count = (
            session.query(TgMessage)
            .filter(
                TgMessage.telegram_chat_id == chat_id,
                TgMessage.id > message.id,
            )
            .count()
        )

    return jsonify({"ok": True, "message": payload, "newer_count": newer_count})


@bp.get("/<chat_id_raw>/messages/context")
@require_panel_auth
def get_message_context(chat_id_raw):
    """
    Load a window of messages around a Telegram message id
    (?around_id=<telegram_message_id>&limit=25), so the panel can jump to
    an original message that was not loaded yet.
    """
    chat_id = parse_chat_id(chat_id_raw)
    if chat_id is None:
        return error(400, "Invalid chat id")

    try:
        around_id = int(request.args.get("around_id", ""))
    except (TypeError, ValueError):
        return error(400, "around_id (Telegram message id) is required")

    limit = get_int_arg("limit", 25, 1, 100)

    with db_session() as session:
        anchor = (
            session.query(TgMessage)
            .filter(
                TgMessage.telegram_chat_id == chat_id,
                TgMessage.telegram_message_id == around_id,
            )
            .one_or_none()
        )
        if anchor is None:
            return error(
                404,
                "Original message is not available in this conversation",
                reason="not_stored",
            )

        half = max(1, limit // 2)
        older = (
            session.query(TgMessage)
            .filter(
                TgMessage.telegram_chat_id == chat_id,
                TgMessage.id < anchor.id,
            )
            .order_by(TgMessage.id.desc())
            .limit(half)
            .all()
        )
        newer = (
            session.query(TgMessage)
            .filter(
                TgMessage.telegram_chat_id == chat_id,
                TgMessage.id > anchor.id,
            )
            .order_by(TgMessage.id.asc())
            .limit(half)
            .all()
        )
        window = list(reversed(newer)) + [anchor] + list(older)  # newest first
        messages = serialize_messages(session, window)

    return jsonify(
        {
            "ok": True,
            "anchor_telegram_message_id": around_id,
            "messages": messages,
            "count": len(messages),
        }
    )

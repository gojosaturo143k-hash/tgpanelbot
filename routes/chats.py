"""
Web Panel API - generic chat operations.

    POST /api/chats/<chat_id>/read
        -> reset that conversation's unread_count to 0.
           Works for both groups and private DMs, keyed by the exact
           Telegram chat id so the correct chat is always updated.
"""

import logging

from flask import Blueprint, jsonify

from database.db import db_session
from database.models import TgChat
from utils.auth import require_panel_auth
from utils.helpers import error, parse_chat_id

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

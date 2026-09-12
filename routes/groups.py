"""
Web Panel API - Telegram groups & supergroups.

    GET  /api/groups                     list groups (search: ?q=)
    GET  /api/groups/<chat_id>/messages  group history (search: ?q=, paging: ?limit=&before_id=)
    POST /api/groups/<chat_id>/reply     send a panel reply into the group
"""

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import String, cast, or_

from database.db import db_session
from database.models import TgChat, TgMessage, serialize_messages
from services.telegram_service import TelegramAPIError, send_message
from telegram.handlers import GROUP_TYPES, DuplicateMessageError, save_outgoing_message
from utils.auth import require_panel_auth
from utils.helpers import (
    error,
    get_int_arg,
    get_search_query,
    mark_chat_inactive,
    parse_chat_id,
    telegram_error_response,
)

log = logging.getLogger(__name__)

bp = Blueprint("groups", __name__, url_prefix="/api/groups")


def _load_group_chat(session, chat_id):
    return (
        session.query(TgChat)
        .filter(TgChat.telegram_chat_id == chat_id)
        .one_or_none()
    )


@bp.get("")
@require_panel_auth
def list_groups():
    """List group/supergroup chats, newest activity first. ?q= searches
    by group title or Telegram chat id."""
    q = get_search_query()
    limit = get_int_arg("limit", 50, 1, 200)
    offset = get_int_arg("offset", 0, 0)

    with db_session() as session:
        base = session.query(TgChat).filter(TgChat.type.in_(GROUP_TYPES))
        if q:
            like = "%" + q + "%"
            base = base.filter(
                or_(
                    TgChat.title.ilike(like),
                    TgChat.username.ilike(like),
                    cast(TgChat.telegram_chat_id, String).ilike(like),
                )
            )
        total = base.count()
        rows = (
            base.order_by(TgChat.updated_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        groups = [chat.to_dict() for chat in rows]

    return jsonify({"ok": True, "groups": groups, "count": len(groups), "total": total})


@bp.get("/<chat_id_raw>/messages")
@require_panel_auth
def group_messages(chat_id_raw):
    """Message history of one group, newest first. Supports ?q= text
    search and ?before_id= cursor pagination."""
    chat_id = parse_chat_id(chat_id_raw)
    if chat_id is None:
        return error(400, "Invalid chat id")

    limit = get_int_arg("limit", 50, 1, 200)
    q = get_search_query()
    before_raw = request.args.get("before_id")
    before_id = None
    if before_raw is not None:
        try:
            before_id = int(before_raw)
        except (TypeError, ValueError):
            return error(400, "Invalid before_id")

    with db_session() as session:
        chat = _load_group_chat(session, chat_id)
        if chat is None:
            return error(404, "Chat not found for this chat id")
        if chat.type not in GROUP_TYPES:
            return error(
                400,
                "This chat id is a private conversation. "
                "Use /api/dms/<chat_id>/messages instead.",
            )
        chat_data = chat.to_dict()

        base = session.query(TgMessage).filter(TgMessage.telegram_chat_id == chat_id)
        if q:
            base = base.filter(TgMessage.text.ilike("%" + q + "%"))
        if before_id is not None:
            base = base.filter(TgMessage.id < before_id)

        rows = base.order_by(TgMessage.id.desc()).limit(limit + 1).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        # Enriched serialization: real sender info, reply relationships
        # and signed media URLs for stickers/voice notes.
        messages = serialize_messages(session, rows)

    return jsonify(
        {
            "ok": True,
            "chat": chat_data,
            "messages": messages,
            "count": len(messages),
            "has_more": has_more,
        }
    )


@bp.post("/<chat_id_raw>/reply")
@require_panel_auth
def reply_to_group(chat_id_raw):
    """
    Send a Web Panel reply into a group/supergroup.

    Safety: the chat id is validated against the database and must belong
    to a group/supergroup BEFORE anything is sent to Telegram, so a reply
    can never accidentally land in the wrong conversation type.
    """
    chat_id = parse_chat_id(chat_id_raw)
    if chat_id is None:
        return error(400, "Invalid chat id")

    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return error(400, "Field 'text' is required and cannot be empty")
    if len(text) > 4096:
        return error(400, "Telegram text messages are limited to 4096 characters")

    # Optional REAL Telegram message_id the panel is replying to.
    raw_reply_to = body.get("reply_to_message_id")
    reply_to_message_id = None
    if raw_reply_to is not None:
        try:
            reply_to_message_id = int(raw_reply_to)
        except (TypeError, ValueError):
            return error(400, "reply_to_message_id must be a Telegram message id")

    with db_session() as session:
        chat = _load_group_chat(session, chat_id)
        if chat is None:
            return error(404, "Group not found for this chat id")
        if chat.type not in GROUP_TYPES:
            return error(
                400,
                "This chat id is not a group/supergroup. Refusing to send. "
                "Use /api/dms/<chat_id>/reply for private chats.",
            )

        # The reply target must belong to THIS exact Telegram chat -
        # never a database id, user id or a message from another group.
        if reply_to_message_id is not None:
            target = (
                session.query(TgMessage)
                .filter(
                    TgMessage.telegram_chat_id == chat_id,
                    TgMessage.telegram_message_id == reply_to_message_id,
                )
                .one_or_none()
            )
            if target is None:
                return error(
                    400,
                    "reply_to_message_id does not belong to a known message "
                    "in this chat",
                )

    log.info(
        "Web Panel sending group reply (chat_id=%s, reply_to=%s)",
        chat_id,
        reply_to_message_id,
    )
    try:
        telegram_result = send_message(
            chat_id, text, reply_to_message_id=reply_to_message_id
        )
    except TelegramAPIError as exc:
        if exc.status_code == 403:
            mark_chat_inactive(chat_id)
        log.warning(
            "Group reply failed (chat_id=%s): %s", chat_id, exc.message
        )
        return telegram_error_response(exc)

    log.info(
        "Group reply sent via Telegram (chat_id=%s, telegram_message_id=%s, "
        "reply_to=%s)",
        chat_id,
        telegram_result.get("message_id"),
        reply_to_message_id,
    )

    stored = None
    try:
        stored = save_outgoing_message(
            chat_id, telegram_result, text, reply_to_message_id=reply_to_message_id
        )
    except DuplicateMessageError:
        log.info("Outgoing group reply already stored (chat_id=%s)", chat_id)
    except Exception:  # noqa: BLE001 - the message WAS delivered, stay honest
        log.exception(
            "Group reply was delivered but could not be stored in the database "
            "(chat_id=%s)",
            chat_id,
        )

    response = {
        "ok": True,
        "chat_id": chat_id,
        "telegram_message_id": telegram_result.get("message_id"),
        "reply_to_message_id": reply_to_message_id,
    }
    if stored:
        response["message"] = stored
    else:
        response["warning"] = "Delivered to Telegram but not stored in the database"
    return jsonify(response)

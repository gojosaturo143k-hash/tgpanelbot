"""
Web Panel API - dashboard summary.

    GET /api/dashboard
        -> total groups, total private DMs, total messages, unread total,
           incoming/outgoing counts and the most recent activity.
"""

import logging

from flask import Blueprint, jsonify
from sqlalchemy import func

from database.db import db_session
from database.models import TgChat, TgMessage
from telegram.handlers import GROUP_TYPES
from utils.auth import require_panel_auth

log = logging.getLogger(__name__)

bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@bp.get("")
@require_panel_auth
def dashboard():
    with db_session() as session:
        total_groups = (
            session.query(func.count(TgChat.id))
            .filter(TgChat.type.in_(GROUP_TYPES))
            .scalar()
        ) or 0

        total_dms = (
            session.query(func.count(TgChat.id))
            .filter(TgChat.type == "private")
            .scalar()
        ) or 0

        total_messages = session.query(func.count(TgMessage.id)).scalar() or 0

        unread_messages = (
            session.query(func.coalesce(func.sum(TgChat.unread_count), 0)).scalar()
        ) or 0

        incoming_messages = (
            session.query(func.count(TgMessage.id))
            .filter(TgMessage.direction == "incoming")
            .scalar()
        ) or 0

        outgoing_messages = (
            session.query(func.count(TgMessage.id))
            .filter(TgMessage.direction == "outgoing")
            .scalar()
        ) or 0

        recent_rows = (
            session.query(TgMessage).order_by(TgMessage.id.desc()).limit(12).all()
        )
        chat_ids = {m.telegram_chat_id for m in recent_rows}
        chats = {}
        if chat_ids:
            for chat in (
                session.query(TgChat)
                .filter(TgChat.telegram_chat_id.in_(chat_ids))
                .all()
            ):
                chats[chat.telegram_chat_id] = chat

        recent_activity = []
        for message in recent_rows:
            entry = message.to_dict()
            chat = chats.get(message.telegram_chat_id)
            entry["chat_title"] = chat.title if chat else None
            entry["chat_type"] = chat.type if chat else None
            recent_activity.append(entry)

    return jsonify(
        {
            "ok": True,
            "total_groups": total_groups,
            "total_dms": total_dms,
            "total_messages": total_messages,
            "unread_messages": int(unread_messages),
            "incoming_messages": incoming_messages,
            "outgoing_messages": outgoing_messages,
            "recent_activity": recent_activity,
        }
    )

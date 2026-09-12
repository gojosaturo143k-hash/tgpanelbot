"""
Web Panel API - recent stickers.

    GET /api/stickers/recent?limit=30[&chat_id=...]

A Telegram Bot API bot is NOT a user account: it cannot read the
operator's personal sticker library or "recently used" keyboard. What it
CAN do is re-send any sticker file_id it has already seen. This endpoint
therefore returns distinct stickers previously received (or sent) by the
bot, newest first, ready to be re-sent via the sticker endpoints.
"""

import logging

from flask import Blueprint, jsonify

from database.db import db_session
from database.models import TgMessage
from services.telegram_service import build_media_url
from utils.auth import require_panel_auth
from utils.helpers import get_int_arg, parse_chat_id
from flask import request

log = logging.getLogger(__name__)

bp = Blueprint("stickers", __name__, url_prefix="/api/stickers")


@bp.get("/recent")
@require_panel_auth
def recent_stickers():
    limit = get_int_arg("limit", 30, 1, 100)

    chat_filter = None
    raw_chat = request.args.get("chat_id")
    if raw_chat:
        chat_filter = parse_chat_id(raw_chat)

    with db_session() as session:
        query = session.query(TgMessage).filter(TgMessage.message_type == "sticker")
        if chat_filter is not None:
            query = query.filter(TgMessage.telegram_chat_id == chat_filter)
        # Scan a generous window, then de-duplicate by file_unique_id.
        rows = query.order_by(TgMessage.id.desc()).limit(limit * 6).all()

    seen = set()
    stickers = []
    for row in rows:
        media = row.media or {}
        file_id = media.get("file_id")
        if not file_id:
            continue
        key = media.get("file_unique_id") or file_id
        if key in seen:
            continue
        seen.add(key)

        item = dict(media)
        item["url"] = build_media_url(file_id)
        item["last_seen_chat_id"] = row.telegram_chat_id
        item["last_seen_at"] = (
            row.telegram_timestamp.isoformat() if row.telegram_timestamp else None
        )
        stickers.append(item)
        if len(stickers) >= limit:
            break

    return jsonify({"ok": True, "stickers": stickers, "count": len(stickers)})

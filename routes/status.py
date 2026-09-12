"""
Web Panel API - backend status.

    GET /api/status
        -> honest information about backend, Telegram and database state.
           Reports real failures instead of faking an "online" status.
"""

import logging
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify

import live
from config import Config
from database.db import check_database
from services.telegram_service import TelegramAPIError, get_me
from utils.auth import require_panel_auth

log = logging.getLogger(__name__)

bp = Blueprint("status", __name__, url_prefix="/api/status")

# Small in-memory cache so /api/status does not hammer the Telegram API.
_TELEGRAM_CHECK_TTL = 60  # seconds
_telegram_cache = {"checked_at": 0.0, "connected": False, "username": None}


def _check_telegram():
    """Check Telegram reachability (cached for _TELEGRAM_CHECK_TTL seconds)."""
    now = time.time()
    if now - _telegram_cache["checked_at"] < _TELEGRAM_CHECK_TTL:
        return dict(_telegram_cache)

    _telegram_cache["checked_at"] = now
    if not Config.telegram_configured():
        _telegram_cache.update(connected=False, username=None)
        return dict(_telegram_cache)

    try:
        me = get_me(timeout=5)
        _telegram_cache.update(connected=True, username=me.get("username"))
    except TelegramAPIError as exc:
        log.warning("Telegram status check failed: %s", exc.message)
        _telegram_cache.update(connected=False, username=None)
    return dict(_telegram_cache)


@bp.get("")
@require_panel_auth
def status():
    telegram = _check_telegram()
    database_ok = check_database()

    healthy = database_ok and (
        not Config.telegram_configured() or telegram["connected"]
    )

    payload = {
        "ok": True,
        "backend": "online",
        "service": "telegram-bot",
        "telegram_configured": Config.telegram_configured(),
        "telegram_connected": telegram["connected"],
        "bot_username": telegram["username"],
        "bot_polling": bool(live.state.get("bot_polling")),
        "database": "connected" if database_ok else "error",
        "healthy": healthy,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    # 200 when everything needed works, 503 when something is degraded,
    # so callers and monitors can react to real problems.
    return jsonify(payload), (200 if healthy else 503)

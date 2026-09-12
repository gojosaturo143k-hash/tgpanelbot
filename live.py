"""
live.py - lightweight Flask HTTP server.

Keeps the Render Web Service HTTP-accessible and exposes:

    GET /ping    -> {"pong": true, ...}   (public, for uptime checks)
    GET /health  -> safe health info       (public, no secrets)
    /api/*       -> Web Panel REST API     (Bearer PANEL_API_KEY protected)

The server binds to 0.0.0.0:$PORT (PORT is injected by Render, default
10000). It runs in a background thread inside bot.py, or standalone via:

    python live.py

No Gunicorn. Threads are enabled so concurrent panel requests work fine.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS

from config import Config

log = logging.getLogger("live")

# Shared runtime state (written by bot.py, read by health/status routes).
state = {
    "bot_polling": False,
    "bot_username": None,
    "started_at": time.time(),
}


def create_app():
    app = Flask(__name__)

    # CORS only for the Web Panel API, and only for the configured origin.
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": Config.allowed_origins(),
                "allow_headers": ["Authorization", "Content-Type", "X-API-Key"],
                "methods": ["GET", "POST", "OPTIONS"],
            }
        },
    )

    # API blueprints are imported here (not at module top) to avoid any
    # circular import with routes that read `live.state`.
    from routes import register_blueprints

    register_blueprints(app)

    # ------------------------------------------------------------------
    # Public uptime endpoints
    # ------------------------------------------------------------------

    @app.get("/ping")
    def ping():
        log.info("Ping request from %s", request.remote_addr)
        return jsonify({"pong": True, "service": "telegram-bot"})

    @app.get("/health")
    def health():
        from database.db import check_database

        database_ok = check_database()
        telegram_ok = Config.telegram_configured()
        payload = {
            # Never expose tokens, passwords or URLs containing secrets.
            "status": "ok" if database_ok else "degraded",
            "service": "telegram-bot",
            "telegram_configured": telegram_ok,
            "bot_polling": bool(state.get("bot_polling")),
            "database": "connected" if database_ok else "error",
            "uptime_seconds": int(time.time() - state["started_at"]),
            "time": datetime.now(timezone.utc).isoformat(),
        }
        # Always 200 so Render keeps the service alive; callers can judge
        # by the fields. /api/status reports richer details for the panel.
        return jsonify(payload)

    # ------------------------------------------------------------------
    # JSON error handlers (never leak stack traces to clients)
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(_err):
        return jsonify({"ok": False, "error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_err):
        return jsonify({"ok": False, "error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(err):
        log.exception("Unhandled server error: %s", getattr(err, "original_exception", err))
        return jsonify({"ok": False, "error": "Internal server error"}), 500

    return app


def run_server():
    """Run the Flask server in the CURRENT thread (blocking)."""
    port = Config.PORT
    app = create_app()
    log.info("Flask live server listening on 0.0.0.0:%s", port)
    # threaded=True -> concurrent panel/API requests are handled in parallel.
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


def start_in_background():
    """Start the Flask server in a daemon thread and return immediately."""
    thread = threading.Thread(target=run_server, name="live-server", daemon=True)
    thread.start()
    log.info("Flask live server started in background thread")
    return thread


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    log.info("Starting standalone live server (Telegram bot is NOT running)")
    run_server()

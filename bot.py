"""
bot.py - main entry point of the Telegram Bot Backend.

Runtime architecture (single Render Web Service process, no Gunicorn):

    python bot.py
        |
        |-- 1. initialize the PostgreSQL database (tables are created
        |      when missing)
        |-- 2. start live.py's Flask server in a background thread
        |      (binds 0.0.0.0:$PORT, serves /ping, /health and /api/*)
        `-- 3. run the Telegram bot with long polling (getUpdates)
               in the main thread (no webhook URL needed)

Incoming messages are stored in PostgreSQL and surfaced to the separate
Web Panel through the REST API. The bot never auto-replies.
"""

import logging
import sys
import threading
import time

import live
from config import Config
from database.db import (
    DatabaseNotConfiguredError,
    db_session,  # noqa: F401  (kept for convenience/future use)
    init_db,
)
from sqlalchemy.exc import SQLAlchemyError

from services.telegram_service import TelegramAPIError, get_me, get_updates
from telegram.handlers import process_update

log = logging.getLogger("bot")

LONG_POLL_TIMEOUT = 30  # seconds Telegram holds the getUpdates request


# ----------------------------------------------------------------------
# Startup helpers
# ----------------------------------------------------------------------

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    # Keep noisy libraries quiet and never log request URLs (they could
    # contain the bot token in Telegram API paths).
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def ensure_database(max_attempts=5):
    """Create tables, retrying briefly so boot survives slow DB startups."""
    if not Config.database_configured():
        log.error("DATABASE_URL is not configured - the bot cannot store messages")
        return False
    for attempt in range(1, max_attempts + 1):
        try:
            init_db()
            return True
        except Exception as exc:  # noqa: BLE001 - log class only, no DSN secrets
            log.error(
                "Database init failed (attempt %s/%s): %s",
                attempt,
                max_attempts,
                exc.__class__.__name__,
            )
            time.sleep(4)
    log.error("Continuing WITHOUT a database; /health and /api/status will report it")
    return False


def _is_db_error(exc):
    return isinstance(exc, (DatabaseNotConfiguredError, SQLAlchemyError))


# ----------------------------------------------------------------------
# Telegram long polling
# ----------------------------------------------------------------------

def polling_loop():
    """Receive Telegram updates forever with resilient error handling."""
    try:
        me = get_me(timeout=10)
        live.state["bot_username"] = me.get("username")
        log.info("Telegram bot connected as @%s", me.get("username"))
    except TelegramAPIError as exc:
        log.warning(
            "Could not reach Telegram yet (%s) - polling will retry automatically",
            exc.message,
        )

    live.state["bot_polling"] = True
    log.info("Telegram bot started (long polling, timeout=%ss)", LONG_POLL_TIMEOUT)

    offset = None
    backoff = 1  # seconds; grows on consecutive failures

    while True:
        try:
            updates = get_updates(offset=offset, timeout=LONG_POLL_TIMEOUT)
            backoff = 1

            for update in updates:
                update_id = update.get("update_id")
                try:
                    process_update(update)
                except Exception as exc:  # noqa: BLE001 - keep the bot alive
                    if _is_db_error(exc):
                        # Do NOT advance the offset: leave this update (and
                        # the ones after it) unconfirmed so Telegram
                        # redelivers them once the database is back.
                        log.error(
                            "Database error while storing update %s - "
                            "will retry later",
                            update_id,
                        )
                        time.sleep(5)
                        break
                    log.exception("Failed to process update %s", update_id)
                    offset = update_id + 1  # skip the poisoned update
                else:
                    offset = update_id + 1

        except TelegramAPIError as exc:
            status = exc.status_code
            if status == 409:
                log.error(
                    "Telegram conflict (409): another bot instance is polling "
                    "with the same token. Stop it. Retrying in 15s."
                )
                time.sleep(15)
            elif status == 401:
                log.error(
                    "Telegram rejected the bot token (401 Unauthorized). "
                    "Fix TELEGRAM_BOT_TOKEN. Retrying in 60s."
                )
                time.sleep(60)
            elif exc.retry_after:
                log.warning("Telegram rate limit - sleeping %ss", exc.retry_after + 1)
                time.sleep(exc.retry_after + 1)
            else:
                log.warning("Telegram polling error: %s (retry in %ss)", exc.message, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

        except Exception as exc:  # noqa: BLE001 - network hiccups etc.
            log.warning(
                "Unexpected polling error (%s) - retrying in %ss",
                exc.__class__.__name__,
                backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def idle_loop():
    """
    Keep the process alive (and the HTTP server reachable) when the bot
    token is missing, so Render stays up and /health can report the
    misconfiguration instead of crash-looping.
    """
    log.error(
        "TELEGRAM_BOT_TOKEN is not configured. The HTTP server stays online "
        "(/ping, /health) but Telegram polling is DISABLED."
    )
    while True:
        time.sleep(3600)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    setup_logging()
    log.info("=== Telegram Bot Backend starting ===")
    log.info(
        "Config: telegram_token=%s database_url=%s panel_api_key=%s frontend_origin=%s port=%s",
        "set" if Config.telegram_configured() else "MISSING",
        "set" if Config.database_configured() else "MISSING",
        "set" if Config.panel_auth_configured() else "MISSING",
        Config.FRONTEND_ORIGIN or "(localhost dev defaults)",
        Config.PORT,
    )

    ensure_database()

    live.start_in_background()
    log.info(
        "Live HTTP endpoints ready: /ping /health /api/* on 0.0.0.0:%s",
        Config.PORT,
    )

    if not Config.telegram_configured():
        idle_loop()

    polling_thread = threading.Thread(
        target=polling_loop, name="telegram-poller", daemon=True
    )
    polling_thread.start()

    # Keep the main thread alive forever; worker threads do the job.
    threading.Event().wait()


if __name__ == "__main__":
    main()

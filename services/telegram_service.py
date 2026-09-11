"""
Thin client around the Telegram Bot HTTP API (via `requests`).

- Uses long polling (getUpdates), so no public webhook URL is required.
- Raises TelegramAPIError with structured details so callers can map
  Telegram errors (rate limits, blocked bot, unknown chat, ...) to clean
  JSON API responses.
- NEVER logs the bot token or the API base URL (which contains it).
"""

import logging

import requests

from config import Config

log = logging.getLogger(__name__)


class TelegramAPIError(Exception):
    """A Telegram Bot API call failed (or Telegram is unreachable)."""

    def __init__(self, message, status_code=None, error_code=None, retry_after=None):
        super().__init__(message)
        # Human readable description from Telegram (safe, no secrets).
        self.message = message
        # HTTP status code of the Telegram response (None for network errors).
        self.status_code = status_code
        # Telegram's numeric error_code when present.
        self.error_code = error_code
        # Seconds to wait, present on HTTP 429 rate limiting.
        self.retry_after = retry_after


def _api_url(method):
    if not Config.telegram_configured():
        raise TelegramAPIError(
            "Telegram bot token is not configured on the server",
            status_code=500,
        )
    return Config.TELEGRAM_API_BASE + "/" + method


def _api_post(method, payload=None, timeout=15):
    """POST a Bot API method and return the `result` field on success."""
    try:
        response = requests.post(
            _api_url(method), json=payload or {}, timeout=timeout
        )
    except requests.RequestException:
        log.error("Telegram API network error calling method '%s'", method)
        raise TelegramAPIError(
            "Network error while contacting Telegram", status_code=502
        )

    try:
        data = response.json()
    except ValueError:
        log.error(
            "Telegram API returned a non-JSON response (HTTP %s) for '%s'",
            response.status_code,
            method,
        )
        raise TelegramAPIError(
            "Invalid response from Telegram", status_code=502
        )

    if not data.get("ok"):
        description = data.get("description", "Unknown Telegram error")
        retry_after = None
        if response.status_code == 429:
            retry_after = (data.get("parameters") or {}).get("retry_after", 5)
            log.warning(
                "Telegram rate limited '%s' (retry in %ss)", method, retry_after
            )
        else:
            log.warning(
                "Telegram API error on '%s': HTTP %s - %s",
                method,
                response.status_code,
                description,
            )
        raise TelegramAPIError(
            description,
            status_code=response.status_code,
            error_code=data.get("error_code"),
            retry_after=retry_after,
        )

    return data.get("result")


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

def get_me(timeout=5):
    """Return basic info about the bot (used for status checks)."""
    return _api_post("getMe", {}, timeout=timeout)


def get_updates(offset=None, timeout=30):
    """
    Long-poll Telegram for new updates.

    `timeout` is Telegram's long-poll seconds; the HTTP request timeout is
    slightly longer so slow polls do not die early.
    """
    payload = {
        "timeout": timeout,
        "allowed_updates": ["message"],  # Phase 1: text messages only
    }
    if offset is not None:
        payload["offset"] = offset
    return _api_post("getUpdates", payload, timeout=timeout + 15)


def send_message(chat_id, text, reply_to_message_id=None):
    """
    Send a text message to an EXACT Telegram chat id.

    The caller is responsible for verifying that `chat_id` belongs to the
    intended conversation type BEFORE calling this (group vs private DM).
    """
    payload = {"chat_id": int(chat_id), "text": text}
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
    return _api_post("sendMessage", payload, timeout=20)

"""
Thin client around the Telegram Bot HTTP API (via `requests`).

- Uses long polling (getUpdates), so no public webhook URL is required.
- Raises TelegramAPIError with structured details so callers can map
  Telegram errors (rate limits, blocked bot, unknown chat, ...) to clean
  JSON API responses.
- NEVER logs the bot token or the API base URL (which contains it).
"""

import hashlib
import hmac
import logging
import time

import requests

from config import Config

log = logging.getLogger(__name__)

# Signed media URLs stay valid for this long (browser <img>/<audio> tags
# cannot send Authorization headers, so /api/media uses these instead).
MEDIA_URL_TTL_SECONDS = 6 * 3600  # 6 hours

# Telegram file_paths returned by getFile are valid for at least ~1 hour.
# We re-resolve a bit earlier to stay safe.
_FILE_PATH_CACHE_TTL = 50 * 60
_file_path_cache = {}  # file_id -> (file_path, resolved_at)

_MEDIA_CONTENT_TYPES = {
    "webp": "image/webp",        # static stickers
    "webm": "video/webm",        # video stickers
    "tgs": "application/x-tgsticker",  # animated (Lottie) stickers
    "oga": "audio/ogg",          # voice notes
    "ogg": "audio/ogg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


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
        # ONLY "message" updates. Deliberately excludes message_reaction /
        # message_reaction_count: reaction monitoring is not implemented
        # and would require the bot to be a group administrator.
        "allowed_updates": ["message"],
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
        # Real Telegram message_id of the target message in THIS chat.
        payload["reply_to_message_id"] = int(reply_to_message_id)
    return _api_post("sendMessage", payload, timeout=20)


def send_sticker(chat_id, sticker_file_id, reply_to_message_id=None):
    """Send a sticker by file_id to an EXACT Telegram chat id."""
    payload = {"chat_id": int(chat_id), "sticker": str(sticker_file_id)}
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
    return _api_post("sendSticker", payload, timeout=20)


def send_voice(chat_id, voice_bytes, filename="voice.ogg", duration=None,
               caption=None, reply_to_message_id=None, timeout=60):
    """
    Send a voice note (multipart upload).

    Telegram requires OGG/Opus for sendVoice; callers must convert or
    validate the format before calling this.
    """
    data = {"chat_id": str(int(chat_id))}
    if reply_to_message_id is not None:
        data["reply_to_message_id"] = str(int(reply_to_message_id))
    if duration:
        data["duration"] = str(int(duration))
    if caption:
        data["caption"] = caption

    files = {"voice": (filename, voice_bytes, "audio/ogg")}

    try:
        response = requests.post(
            _api_url("sendVoice"), data=data, files=files, timeout=timeout
        )
    except requests.RequestException:
        log.error("Telegram API network error while sending a voice note")
        raise TelegramAPIError(
            "Network error while contacting Telegram", status_code=502
        )

    try:
        result = response.json()
    except ValueError:
        raise TelegramAPIError("Invalid response from Telegram", status_code=502)

    if not result.get("ok"):
        description = result.get("description", "Unknown Telegram error")
        retry_after = None
        if response.status_code == 429:
            retry_after = (result.get("parameters") or {}).get("retry_after", 5)
        log.warning(
            "Telegram API error on 'sendVoice': HTTP %s - %s",
            response.status_code,
            description,
        )
        raise TelegramAPIError(
            description,
            status_code=response.status_code,
            error_code=result.get("error_code"),
            retry_after=retry_after,
        )
    return result.get("result")


# ----------------------------------------------------------------------
# Telegram file downloads (media stays 100% server-side)
# ----------------------------------------------------------------------

def get_file(file_id, timeout=15):
    """Resolve a Telegram file_id to file metadata (incl. file_path)."""
    return _api_post("getFile", {"file_id": str(file_id)}, timeout=timeout)


def resolve_file_path(file_id):
    """file_id -> file_path with a short in-memory cache."""
    cached = _file_path_cache.get(str(file_id))
    if cached and (time.time() - cached[1]) < _FILE_PATH_CACHE_TTL:
        return cached[0]
    info = get_file(file_id)
    file_path = info.get("file_path")
    if not file_path:
        raise TelegramAPIError("Telegram returned no file_path", status_code=502)
    _file_path_cache[str(file_id)] = (file_path, time.time())
    return file_path


def guess_media_content_type(file_path):
    ext = (file_path or "").rsplit(".", 1)[-1].lower() if "." in (file_path or "") else ""
    return _MEDIA_CONTENT_TYPES.get(ext, "application/octet-stream")


def open_file_stream(file_path, timeout=30):
    """
    Open a streaming download of a Telegram file.

    The URL contains the bot token, so it is constructed and used here,
    on the server, and is NEVER logged or exposed to clients.
    """
    if not Config.telegram_configured():
        raise TelegramAPIError(
            "Telegram bot token is not configured on the server", status_code=500
        )
    url = (
        "https://api.telegram.org/file/bot"
        + Config.TELEGRAM_BOT_TOKEN
        + "/"
        + file_path
    )
    try:
        response = requests.get(url, stream=True, timeout=30)
    except requests.RequestException:
        log.error("Network error while downloading Telegram media")
        raise TelegramAPIError(
            "Network error while downloading Telegram media", status_code=502
        )
    if response.status_code != 200:
        log.error(
            "Telegram media download failed with HTTP %s", response.status_code
        )
        response.close()
        raise TelegramAPIError(
            "Telegram media download failed (HTTP %s)" % response.status_code,
            status_code=502,
        )
    return response


# ----------------------------------------------------------------------
# Signed media URLs (used by the Web Panel instead of raw file_ids)
# ----------------------------------------------------------------------

def _media_signature(file_id, exp):
    secret = Config.MEDIA_SIGNING_SECRET or "unsigned"
    message = ("%s:%s" % (file_id, exp)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def build_media_url(file_id, ttl=MEDIA_URL_TTL_SECONDS):
    """Return a signed, expiring backend proxy URL for a Telegram file."""
    exp = int(time.time()) + int(ttl)
    sig = _media_signature(file_id, exp)
    return "/api/media/%s?exp=%s&sig=%s" % (file_id, exp, sig)


def verify_media_signature(file_id, exp, sig):
    """True when the signature is present, correct and not expired."""
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if not sig or not Config.MEDIA_SIGNING_SECRET:
        return False
    if exp_int < int(time.time()):
        return False
    expected = _media_signature(file_id, exp_int)
    return hmac.compare_digest(str(sig), expected)

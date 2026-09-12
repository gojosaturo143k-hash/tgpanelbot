"""
Shared send pipeline for panel -> Telegram messages (sticker / voice).

Keeps groups.py and dms.py thin and guarantees both conversation kinds
behave identically, while still refusing to cross chat types: a group
send must target a group, a DM send must target a private chat.

Existing text replies keep their own (already working) implementation.
"""

import logging
import shutil
import subprocess
import tempfile

from flask import jsonify, request

from database.db import db_session
from database.models import TgChat, TgMessage
from services.telegram_service import (
    TelegramAPIError,
    send_sticker,
    send_voice,
)
from telegram.handlers import (
    GROUP_TYPES,
    DuplicateMessageError,
    save_outgoing_message,
)
from utils.helpers import error, mark_chat_inactive, telegram_error_response

log = logging.getLogger(__name__)

# Formats Telegram accepts directly for sendVoice.
_OGG_EXTENSIONS = (".ogg", ".oga", ".opus")
MAX_VOICE_BYTES = 20 * 1024 * 1024  # Telegram's practical upload limit


def parse_reply_target(body):
    """
    Read an optional reply_to_message_id from a request body.

    Returns (value_or_None, error_response_or_None).
    """
    raw = body.get("reply_to_message_id")
    if raw in (None, "", "null"):
        return None, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, error(400, "reply_to_message_id must be a Telegram message id")


def validate_chat(session, chat_id, want):
    """
    Load a chat and confirm it is the expected kind.

    want: "group" (group/supergroup) or "private".
    Returns (chat, error_response_or_None).
    """
    chat = (
        session.query(TgChat)
        .filter(TgChat.telegram_chat_id == chat_id)
        .one_or_none()
    )
    if chat is None:
        return None, error(404, "Chat not found for this chat id")

    if want == "group" and chat.type not in GROUP_TYPES:
        return None, error(
            400,
            "This chat id is not a group/supergroup. Refusing to send. "
            "Use the /api/dms/... endpoint for private chats.",
        )
    if want == "private" and chat.type != "private":
        return None, error(
            400,
            "This chat id is not a private conversation. Refusing to send. "
            "Use the /api/groups/... endpoint for groups.",
        )
    return chat, None


def validate_reply_target(session, chat_id, reply_to_message_id):
    """The reply target must be a real message in THIS exact chat."""
    if reply_to_message_id is None:
        return None
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
            "reply_to_message_id does not belong to a known message in this chat",
        )
    return None


def _store_and_respond(chat_id, telegram_result, message_type, text,
                       reply_to_message_id):
    """Persist an outgoing message and build the JSON response."""
    stored = None
    try:
        stored = save_outgoing_message(
            chat_id,
            telegram_result,
            text,
            reply_to_message_id=reply_to_message_id,
            message_type=message_type,
        )
    except DuplicateMessageError:
        log.info("Outgoing %s already stored (chat_id=%s)", message_type, chat_id)
    except Exception:  # noqa: BLE001 - it WAS delivered; stay honest
        log.exception(
            "Outgoing %s was delivered but could not be stored (chat_id=%s)",
            message_type,
            chat_id,
        )

    response = {
        "ok": True,
        "chat_id": chat_id,
        "telegram_message_id": telegram_result.get("message_id"),
        "message_type": message_type,
        "reply_to_message_id": reply_to_message_id,
    }
    if stored:
        response["message"] = stored
    else:
        response["warning"] = "Delivered to Telegram but not stored in the database"
    return jsonify(response)


# ----------------------------------------------------------------------
# Sticker
# ----------------------------------------------------------------------

def handle_send_sticker(chat_id, want):
    """POST body: {"file_id": "...", "reply_to_message_id": 123 (optional)}"""
    body = request.get_json(silent=True) or {}
    file_id = str(body.get("file_id") or body.get("sticker") or "").strip()
    if not file_id:
        return error(400, "Field 'file_id' is required")

    reply_to_message_id, err = parse_reply_target(body)
    if err:
        return err

    with db_session() as session:
        _, err = validate_chat(session, chat_id, want)
        if err:
            return err
        err = validate_reply_target(session, chat_id, reply_to_message_id)
        if err:
            return err

    log.info(
        "Web Panel sending sticker (chat_id=%s, reply_to=%s)",
        chat_id,
        reply_to_message_id,
    )
    try:
        result = send_sticker(
            chat_id, file_id, reply_to_message_id=reply_to_message_id
        )
    except TelegramAPIError as exc:
        if exc.status_code == 403:
            mark_chat_inactive(chat_id)
        if exc.status_code == 400:
            # Most common cause: the file_id is not a valid sticker.
            return error(
                400,
                "Telegram rejected the sticker: %s" % exc.message,
            )
        return telegram_error_response(exc)

    log.info(
        "Sticker sent (chat_id=%s, telegram_message_id=%s)",
        chat_id,
        result.get("message_id"),
    )
    return _store_and_respond(chat_id, result, "sticker", None, reply_to_message_id)


# ----------------------------------------------------------------------
# Voice
# ----------------------------------------------------------------------

def _ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _convert_to_ogg_opus(raw_bytes, suffix):
    """
    Convert a browser recording (usually WebM/Opus) to OGG/Opus.

    Uses ffmpeg when it is available on the host - no heavy dependency is
    added; when ffmpeg is missing the caller reports a clear error instead.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix or ".webm") as src, \
            tempfile.NamedTemporaryFile(suffix=".ogg") as dst:
        src.write(raw_bytes)
        src.flush()
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", src.name,
            "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
            "-f", "ogg", dst.name,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            log.warning("ffmpeg failed to convert a panel voice recording")
            return None
        with open(dst.name, "rb") as handle:
            return handle.read()


def handle_send_voice(chat_id, want):
    """
    Multipart upload: file field "voice" (plus optional form fields
    "reply_to_message_id", "duration", "caption").

    OGG/Opus is sent straight through; other formats (e.g. browser
    MediaRecorder WebM/Opus) are converted with ffmpeg when available.
    """
    upload = request.files.get("voice") or request.files.get("file")
    if upload is None:
        return error(400, "A 'voice' file upload is required")

    reply_to_message_id, err = parse_reply_target(request.form)
    if err:
        return err

    duration = request.form.get("duration")
    try:
        duration = int(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    caption = (request.form.get("caption") or "").strip() or None

    raw = upload.read()
    if not raw:
        return error(400, "The uploaded voice file is empty")
    if len(raw) > MAX_VOICE_BYTES:
        return error(413, "Voice note is too large (limit 20 MB)")

    filename = (upload.filename or "voice").lower()
    mimetype = (upload.mimetype or "").lower()
    is_ogg = filename.endswith(_OGG_EXTENSIONS) or "ogg" in mimetype or "opus" in mimetype

    if not is_ogg:
        if not _ffmpeg_available():
            return error(
                400,
                "Unsupported voice format. Telegram voice notes require "
                "OGG/Opus. Upload an .ogg/.opus file, or install ffmpeg on "
                "the server to enable automatic conversion.",
            )
        suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".webm"
        converted = _convert_to_ogg_opus(raw, suffix)
        if converted is None:
            return error(400, "Could not convert the recording to OGG/Opus")
        raw = converted
        log.info("Converted a panel voice recording to OGG/Opus")

    with db_session() as session:
        _, err = validate_chat(session, chat_id, want)
        if err:
            return err
        err = validate_reply_target(session, chat_id, reply_to_message_id)
        if err:
            return err

    log.info(
        "Web Panel sending voice note (chat_id=%s, reply_to=%s, bytes=%s)",
        chat_id,
        reply_to_message_id,
        len(raw),
    )
    try:
        result = send_voice(
            chat_id,
            raw,
            filename="voice.ogg",
            duration=duration,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )
    except TelegramAPIError as exc:
        if exc.status_code == 403:
            mark_chat_inactive(chat_id)
        return telegram_error_response(exc)

    log.info(
        "Voice note sent (chat_id=%s, telegram_message_id=%s)",
        chat_id,
        result.get("message_id"),
    )
    return _store_and_respond(chat_id, result, "voice", caption, reply_to_message_id)

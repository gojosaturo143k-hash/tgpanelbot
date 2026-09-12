"""
Secure Telegram media proxy.

    GET /api/media/<file_id>?exp=<unix>&sig=<hmac>

Why this endpoint exists (necessary, minimal):

- A Telegram file_id is NOT a browser-usable URL. Files must be fetched
  with getFile + a download URL that CONTAINS THE BOT TOKEN.
- The bot token must never reach the browser, so the backend streams the
  bytes itself.
- Media is rendered via <img>/<audio>/<video> tags, which cannot send
  Authorization headers - so instead of the Bearer panel key, message
  payloads embed a short-lived HMAC-signed URL built from the server-side
  MEDIA_SIGNING_SECRET (falls back to PANEL_API_KEY).

The plain JSON /api/* endpoints keep their Bearer authentication - this
endpoint only ever serves the exact Telegram file that was signed into a
recently issued message payload.
"""

import logging

from flask import Blueprint, Response, request, stream_with_context

from services.telegram_service import (
    TelegramAPIError,
    guess_media_content_type,
    open_file_stream,
    resolve_file_path,
    verify_media_signature,
)
from utils.helpers import error

log = logging.getLogger(__name__)

bp = Blueprint("media", __name__, url_prefix="/api/media")


@bp.get("/<path:file_id>")
def media_file(file_id):
    exp = request.args.get("exp")
    sig = request.args.get("sig")

    if not verify_media_signature(file_id, exp, sig):
        log.warning("Rejected media request (bad/expired signature)")
        return error(403, "Invalid or expired media link")

    try:
        file_path = resolve_file_path(file_id)
    except TelegramAPIError as exc:
        log.warning("Could not resolve Telegram media: %s", exc.message)
        if exc.status_code == 400:
            # Telegram no longer knows this file_id (expired/invalid).
            return error(410, "This Telegram media file is no longer available")
        return error(502, "Failed to resolve Telegram media")

    content_type = guess_media_content_type(file_path)

    try:
        upstream = open_file_stream(file_path)
    except TelegramAPIError as exc:
        log.warning("Could not download Telegram media: %s", exc.message)
        return error(502, "Failed to download Telegram media")

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    headers = {
        "Content-Type": content_type,
        # Signed URLs last 6h; browsers may cache safely for a while.
        "Cache-Control": "private, max-age=3600",
    }
    length = upstream.headers.get("Content-Length")
    if length:
        headers["Content-Length"] = length

    log.info("Serving Telegram media (content_type=%s)", content_type)
    return Response(
        stream_with_context(generate()), status=200, headers=headers
    )

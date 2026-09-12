"""
Web Panel API authentication.

All /api/* endpoints are protected with a shared secret:

    Authorization: Bearer <PANEL_API_KEY>

For convenience the header "X-API-Key: <PANEL_API_KEY>" is also accepted.

The key itself is NEVER logged and never returned in any response.
"""

import functools
import hmac
import logging

from flask import jsonify, request

from config import Config

log = logging.getLogger(__name__)


def _extract_key():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return request.headers.get("X-API-Key", "").strip()


def require_panel_auth(view):
    """Decorator: reject requests without a valid PANEL_API_KEY."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not Config.panel_auth_configured():
            log.error("Rejected API call: PANEL_API_KEY is not configured on the server")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Server is missing PANEL_API_KEY configuration",
                    }
                ),
                500,
            )

        provided = _extract_key()
        if not provided or not hmac.compare_digest(provided, Config.PANEL_API_KEY):
            log.warning(
                "Authentication failure for %s %s from %s",
                request.method,
                request.path,
                request.remote_addr,
            )
            return jsonify({"ok": False, "error": "Unauthorized: invalid API key"}), 401

        return view(*args, **kwargs)

    return wrapper

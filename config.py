"""
Central configuration for the Telegram Bot Backend.

Everything is read from environment variables. A local ".env" file is
supported for development via python-dotenv. Real secrets must NEVER be
hardcoded or logged anywhere.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _clean(value):
    """Return a stripped env value ('' when missing)."""
    return (value or "").strip()


def _normalize_database_url(url):
    """
    Normalize DATABASE_URL for SQLAlchemy + psycopg2.

    Render/Heroku sometimes provide URLs starting with "postgres://"
    which SQLAlchemy no longer accepts, so we rewrite them.
    """
    if not url:
        return ""
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


class Config:
    """Static access to all environment-driven settings."""

    # Telegram Bot API token from @BotFather.
    TELEGRAM_BOT_TOKEN = _clean(os.environ.get("TELEGRAM_BOT_TOKEN"))

    # PostgreSQL connection string (normalized for SQLAlchemy).
    DATABASE_URL = _normalize_database_url(_clean(os.environ.get("DATABASE_URL")))

    # Shared secret used to authenticate Web Panel API calls.
    PANEL_API_KEY = _clean(os.environ.get("PANEL_API_KEY"))

    # Secret used to sign media proxy URLs (/api/media/...). Falls back
    # to PANEL_API_KEY so existing deployments keep working without any
    # new environment variable.
    MEDIA_SIGNING_SECRET = _clean(os.environ.get("MEDIA_SIGNING_SECRET")) or _clean(
        os.environ.get("PANEL_API_KEY")
    )

    # Allowed CORS origin of the (separate) Netlify Web Panel.
    FRONTEND_ORIGIN = _clean(os.environ.get("FRONTEND_ORIGIN")).rstrip("/")

    # OPTIONAL: Telegram identity of the panel operator, used only to flag
    # "you were mentioned". When unset, mentions are still parsed and
    # rendered normally - no identity is ever invented.
    OPERATOR_TELEGRAM_USER_ID = _clean(os.environ.get("OPERATOR_TELEGRAM_USER_ID"))
    OPERATOR_TELEGRAM_USERNAME = _clean(
        os.environ.get("OPERATOR_TELEGRAM_USERNAME")
    ).lstrip("@")

    # HTTP port. Render injects PORT automatically; default to 10000.
    try:
        PORT = int(_clean(os.environ.get("PORT")) or "10000")
    except ValueError:
        PORT = 10000

    # Telegram Bot API base URL (contains the token - NEVER log this).
    TELEGRAM_API_BASE = (
        "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN
        if TELEGRAM_BOT_TOKEN
        else ""
    )

    @classmethod
    def telegram_configured(cls):
        return bool(cls.TELEGRAM_BOT_TOKEN)

    @classmethod
    def database_configured(cls):
        return bool(cls.DATABASE_URL)

    @classmethod
    def panel_auth_configured(cls):
        return bool(cls.PANEL_API_KEY)

    @classmethod
    def operator_user_id(cls):
        """Operator's Telegram user id as int, or None when not configured."""
        try:
            return int(cls.OPERATOR_TELEGRAM_USER_ID) if cls.OPERATOR_TELEGRAM_USER_ID else None
        except ValueError:
            return None

    @classmethod
    def operator_configured(cls):
        return bool(cls.operator_user_id() or cls.OPERATOR_TELEGRAM_USERNAME)

    @classmethod
    def allowed_origins(cls):
        """
        CORS origins for the Web Panel API.

        In production only FRONTEND_ORIGIN is allowed. When it is not set
        (local development) we fall back to common localhost dev servers.
        We never use a wildcard in production.
        """
        if cls.FRONTEND_ORIGIN:
            return [cls.FRONTEND_ORIGIN]
        return [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]

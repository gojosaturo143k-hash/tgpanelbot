"""
Telegram update handlers.

Converts raw Telegram updates into database rows:

    Telegram update
        -> identify chat  (create/update tg_chats row)
        -> identify user  (create/update tg_users row)
        -> store message  (tg_messages, duplicate-safe)
        -> increment unread_count
        -> done (NO automatic reply is ever sent)

Idempotency: UNIQUE(telegram_chat_id, telegram_message_id) protects us
when Telegram delivers the same update more than once.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from database.db import db_session
from database.models import TgChat, TgMessage, TgUser

log = logging.getLogger(__name__)

GROUP_TYPES = ("group", "supergroup")


class DuplicateMessageError(Exception):
    """The (chat_id, message_id) pair already exists - safe to ignore."""


def _to_dt(epoch_seconds):
    """Convert Telegram's unix `date` field to an aware datetime."""
    if not epoch_seconds:
        return None
    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc)


def _upsert_user(session, from_data):
    """Create or refresh a tg_users row. Identity = telegram_user_id."""
    if not from_data or not from_data.get("id"):
        return None

    user_id = int(from_data["id"])
    user = (
        session.query(TgUser)
        .filter(TgUser.telegram_user_id == user_id)
        .one_or_none()
    )
    if user is None:
        user = TgUser(
            telegram_user_id=user_id,
            username=from_data.get("username"),
            first_name=from_data.get("first_name"),
            last_name=from_data.get("last_name"),
        )
        session.add(user)
        session.flush()
        log.info("Registered new Telegram user %s", user.display_name())
    else:
        # Keep profile fields fresh (username/names can change).
        user.username = from_data.get("username")
        user.first_name = from_data.get("first_name")
        user.last_name = from_data.get("last_name")
    return user


def _upsert_chat(session, chat_data):
    """Create or refresh a tg_chats row. Identity = telegram_chat_id."""
    chat_id = int(chat_data["id"])
    chat_type = chat_data.get("type") or "private"
    title = chat_data.get("title")

    if chat_type == "private" and not title:
        # Private chats have no title - build a friendly display name.
        name_parts = [
            p for p in [chat_data.get("first_name"), chat_data.get("last_name")] if p
        ]
        title = " ".join(name_parts) or chat_data.get("username")

    chat = (
        session.query(TgChat)
        .filter(TgChat.telegram_chat_id == chat_id)
        .one_or_none()
    )
    if chat is None:
        chat = TgChat(
            telegram_chat_id=chat_id,
            type=chat_type,
            title=title,
            username=chat_data.get("username"),
            active=True,
            unread_count=0,
        )
        session.add(chat)
        session.flush()
        if chat_type in GROUP_TYPES:
            log.info(
                "Group detected and registered: '%s' (chat_id=%s, type=%s)",
                title,
                chat_id,
                chat_type,
            )
        else:
            log.info(
                "Private DM chat registered: '%s' (chat_id=%s)", title, chat_id
            )
    else:
        chat.type = chat_type
        chat.title = title
        chat.username = chat_data.get("username")
        chat.active = True  # we received traffic, so the bot is still present
    return chat


def _extract_message_content(message):
    """
    Determine the message type and build its media payload.

    A Telegram message is NOT always plain text - it can carry a sticker,
    a voice note, etc. Returns (message_type, text, media) where exactly
    one media-ish variant applies. Returns (None, None, None) when the
    update is a type this backend does not handle yet.
    """
    if message.get("text") is not None:
        return "text", message.get("text"), None

    sticker = message.get("sticker")
    if sticker:
        media = {
            "file_id": sticker.get("file_id"),
            "file_unique_id": sticker.get("file_unique_id"),
            "width": sticker.get("width"),
            "height": sticker.get("height"),
            "emoji": sticker.get("emoji"),
            "set_name": sticker.get("set_name"),
            "is_animated": bool(sticker.get("is_animated")),
            "is_video": bool(sticker.get("is_video")),
        }
        return "sticker", None, media

    voice = message.get("voice")
    if voice:
        media = {
            "file_id": voice.get("file_id"),
            "file_unique_id": voice.get("file_unique_id"),
            "duration": voice.get("duration"),
            "mime_type": voice.get("mime_type"),
            "file_size": voice.get("file_size"),
        }
        # Voice notes may carry a caption in Telegram.
        return "voice", message.get("caption"), media

    return None, None, None


def process_update(update):
    """
    Handle one Telegram update object (idempotent).

    Supports text, sticker and voice messages from private chats, groups
    and supergroups. No automatic reply is ever sent.
    """
    message = update.get("message")
    if not message:
        return  # not a message update (edited_message, channel_post, ...)

    chat_data = message.get("chat") or {}
    if "id" not in chat_data:
        return

    message_type, text, media = _extract_message_content(message)
    if message_type is None:
        log.info(
            "Skipping unsupported Telegram message kind (chat_id=%s, "
            "message_id=%s) - additional media types can be added later",
            chat_data.get("id"),
            message.get("message_id"),
        )
        return

    chat_id = int(chat_data["id"])
    message_id = int(message["message_id"])
    chat_type = chat_data.get("type") or "private"

    if chat_type in GROUP_TYPES:
        log.info(
            "Telegram group %s message received (chat_id=%s, message_id=%s)",
            message_type,
            chat_id,
            message_id,
        )
    else:
        log.info(
            "Telegram private DM %s message received (chat_id=%s, message_id=%s)",
            message_type,
            chat_id,
            message_id,
        )

    try:
        with db_session() as session:
            chat = _upsert_chat(session, chat_data)
            _upsert_user(session, message.get("from"))

            record = TgMessage(
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                telegram_user_id=(message.get("from") or {}).get("id"),
                direction="incoming",
                sender_type="user",
                text=text,
                message_type=message_type,
                media=media,
                # Preserve Telegram's real reply relationship, when present.
                reply_to_telegram_message_id=(
                    (message.get("reply_to_message") or {}).get("message_id")
                ),
                telegram_timestamp=_to_dt(message.get("date")),
            )
            session.add(record)
            try:
                session.flush()  # raises IntegrityError on duplicates
            except IntegrityError:
                raise DuplicateMessageError()

            chat.unread_count = (chat.unread_count or 0) + 1

        log.info(
            "Message stored (chat_id=%s, message_id=%s, unread=%s)",
            chat_id,
            message_id,
            (chat.unread_count or 0),
        )
    except DuplicateMessageError:
        # Whole transaction rolled back -> unread_count is NOT increased
        # twice. Processing stays idempotent across Telegram redeliveries.
        log.info(
            "Duplicate Telegram delivery ignored (chat_id=%s, message_id=%s)",
            chat_id,
            message_id,
        )


def save_outgoing_message(chat_id, telegram_result, text, reply_to_message_id=None):
    """
    Persist a message we just sent through the Bot API.

    telegram_result is the Message object returned by sendMessage, so we
    store the real telegram_message_id Telegram assigned to it.

    reply_to_message_id is the REAL Telegram message_id the panel reply
    was attached to (already validated to belong to this chat). It is
    stored explicitly so the relationship survives even if Telegram's
    response omits it.
    """
    chat_id = int(chat_id)
    if reply_to_message_id is None:
        reply_to_message_id = (
            (telegram_result.get("reply_to_message") or {}).get("message_id")
        )
    with db_session() as session:
        # Register the bot itself as a known Telegram user so outgoing
        # messages resolve to a real sender identity as well.
        _upsert_user(session, telegram_result.get("from"))

        record = TgMessage(
            telegram_chat_id=chat_id,
            telegram_message_id=int(telegram_result["message_id"]),
            telegram_user_id=(telegram_result.get("from") or {}).get("id"),
            direction="outgoing",
            sender_type="admin",
            text=text,
            message_type="text",
            reply_to_telegram_message_id=reply_to_message_id,
            telegram_timestamp=_to_dt(telegram_result.get("date")),
        )
        session.add(record)
        try:
            session.flush()
        except IntegrityError:
            raise DuplicateMessageError()
        return record.to_dict()

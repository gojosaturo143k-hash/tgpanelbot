"""
SQLAlchemy models for the Telegram Bot Backend.

Tables:
    tg_chats    - one row per Telegram conversation (private/group/supergroup)
    tg_users    - one row per Telegram user (identity = telegram_user_id)
    tg_messages - every incoming/outgoing message (idempotent via UNIQUE
                  constraint on (telegram_chat_id, telegram_message_id))
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TgChat(Base):
    """A Telegram conversation the bot has seen."""

    __tablename__ = "tg_chats"

    id = Column(Integer, primary_key=True)

    # Real Telegram chat id (e.g. -1001234567890 for supergroups).
    # This is the actual conversation identity.
    telegram_chat_id = Column(BigInteger, unique=True, nullable=False, index=True)

    # "private" | "group" | "supergroup" (also "channel" if ever needed)
    type = Column(String(20), nullable=False)

    # Group/supergroup title, or the user's display name for private chats.
    title = Column(String(255), nullable=True)

    # Public @username of the chat when available.
    username = Column(String(255), nullable=True)

    # False when the bot was removed/blocked from this chat.
    active = Column(Boolean, nullable=False, default=True, server_default="true")

    # Number of incoming messages not yet marked as read by the Web Panel.
    unread_count = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def display_name(self):
        return self.title or self.username or str(self.telegram_chat_id)

    def to_dict(self):
        return {
            "id": self.id,
            "telegram_chat_id": self.telegram_chat_id,
            "type": self.type,
            "title": self.title,
            "username": self.username,
            "active": self.active,
            "unread_count": self.unread_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TgUser(Base):
    """
    A Telegram user.

    IMPORTANT: telegram_user_id is the unique identity. Usernames can
    change, so username is NEVER used as the primary identity.
    """

    __tablename__ = "tg_users"

    id = Column(Integer, primary_key=True)

    telegram_user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def display_name(self):
        parts = [p for p in [self.first_name, self.last_name] if p]
        if parts:
            return " ".join(parts)
        if self.username:
            return "@" + self.username
        return str(self.telegram_user_id)

    def to_dict(self):
        return {
            "id": self.id,
            "telegram_user_id": self.telegram_user_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "display_name": self.display_name(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TgMessage(Base):
    """A single Telegram message (incoming or outgoing)."""

    __tablename__ = "tg_messages"

    id = Column(Integer, primary_key=True)

    telegram_chat_id = Column(BigInteger, nullable=False, index=True)
    telegram_message_id = Column(BigInteger, nullable=False)

    # Sender's Telegram user id. For outgoing messages this is the bot's id.
    telegram_user_id = Column(BigInteger, nullable=True)

    # "incoming" | "outgoing"
    direction = Column(String(10), nullable=False)

    # "user" (telegram user) | "admin" (web panel operator)
    sender_type = Column(String(10), nullable=False)

    text = Column(Text, nullable=True)

    # "text" | "sticker" | "voice" (more media types fit the same pattern)
    message_type = Column(String(20), nullable=False, default="text", server_default="text")

    # Media metadata for non-text messages (sticker/voice/...).
    # Raw Telegram object fields ONLY - never contains the bot token.
    # Example (sticker): {"file_id": "...", "file_unique_id": "...",
    #                     "width": 512, "height": 512, "emoji": "...",
    #                     "set_name": "...", "is_animated": false, "is_video": false}
    # Example (voice):   {"file_id": "...", "file_unique_id": "...",
    #                     "duration": 3, "mime_type": "audio/ogg", "file_size": 12345}
    media = Column(JSONB, nullable=True)

    reply_to_telegram_message_id = Column(BigInteger, nullable=True)

    # Original Telegram send time (message.date from Telegram).
    telegram_timestamp = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # Duplicate protection: Telegram can deliver the same update twice;
        # this UNIQUE constraint makes message processing idempotent.
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_tg_messages_chat_message",
        ),
        Index("ix_tg_messages_chat_id_desc", "telegram_chat_id", "id"),
    )

    def to_dict(self):
        # Additive only - every field the deployed Web Panel already
        # consumes keeps its exact name and meaning.
        return {
            "id": self.id,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_message_id": self.telegram_message_id,
            "telegram_user_id": self.telegram_user_id,
            "direction": self.direction,
            "sender_type": self.sender_type,
            "text": self.text,
            "message_type": self.message_type,
            # Convenience aliases so the panel can use short names.
            "type": self.message_type,
            "reply_to_message_id": self.reply_to_telegram_message_id,
            "reply_to_telegram_message_id": self.reply_to_telegram_message_id,
            # Raw stored media metadata (sticker/voice) or None.
            "media": self.media,
            "telegram_timestamp": (
                self.telegram_timestamp.isoformat() if self.telegram_timestamp else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ----------------------------------------------------------------------
# Batch serialization helpers (sender / reply_to / media enrichment)
# ----------------------------------------------------------------------

def sender_display_name(user, user_id):
    """
    Real display name priority (NO fake names, ever):
        first_name + last_name -> first_name -> username -> user_id
    """
    if user is not None:
        parts = [p for p in [user.first_name, user.last_name] if p]
        if parts:
            return " ".join(parts)
        if user.username:
            return user.username
    if user_id is None:
        return None
    return str(user_id)


def sender_payload(user, user_id):
    """Normalized sender block returned with each message."""
    return {
        "user_id": user_id,
        "first_name": user.first_name if user else None,
        "last_name": user.last_name if user else None,
        "username": user.username if user else None,
        "display_name": sender_display_name(user, user_id),
    }


def media_payload(message):
    """
    Normalized media block for the Web Panel.

    Adds a signed, expiring backend proxy URL - a Telegram file_id is NOT
    a browser-usable URL and the bot token must stay server-side, so the
    panel renders media through /api/media/<file_id>?exp=...&sig=...
    """
    if not message.media:
        return None
    from services.telegram_service import build_media_url

    payload = dict(message.media)
    file_id = payload.get("file_id")
    if file_id:
        payload["url"] = build_media_url(file_id)
    return payload


def serialize_messages(session, messages):
    """
    Serialize message rows with everything the Web Panel needs:

    - "sender": real Telegram user info for every message (no N+1 queries)
    - "sticker" / "voice": media block with a usable signed proxy URL
    - "reply_to_message_id" + "reply_to": the replied-to message with its
      own sender, text and media type, when it exists in this chat
    """
    rows = list(messages)

    user_ids = {m.telegram_user_id for m in rows if m.telegram_user_id is not None}
    reply_keys = {
        (m.telegram_chat_id, m.reply_to_telegram_message_id)
        for m in rows
        if m.reply_to_telegram_message_id is not None
    }

    # Batch-load the replied-to messages (same chat + telegram message id).
    targets = {}
    if reply_keys:
        chat_ids = {chat_id for chat_id, _ in reply_keys}
        msg_ids = {msg_id for _, msg_id in reply_keys}
        target_rows = (
            session.query(TgMessage)
            .filter(
                TgMessage.telegram_chat_id.in_(chat_ids),
                TgMessage.telegram_message_id.in_(msg_ids),
            )
            .all()
        )
        for target in target_rows:
            key = (target.telegram_chat_id, target.telegram_message_id)
            if key in reply_keys:
                targets[key] = target
        user_ids |= {
            t.telegram_user_id for t in targets.values()
            if t.telegram_user_id is not None
        }

    users = {}
    if user_ids:
        for user in (
            session.query(TgUser).filter(TgUser.telegram_user_id.in_(user_ids)).all()
        ):
            users[user.telegram_user_id] = user

    serialized = []
    for message in rows:
        data = message.to_dict()
        data["sender"] = sender_payload(
            users.get(message.telegram_user_id), message.telegram_user_id
        )
        if message.message_type == "sticker":
            data["sticker"] = media_payload(message)
            data["voice"] = None
        elif message.message_type == "voice":
            data["voice"] = media_payload(message)
            data["sticker"] = None
        else:
            data["sticker"] = None
            data["voice"] = None

        reply_to = None
        reply_id = message.reply_to_telegram_message_id
        if reply_id is not None:
            target = targets.get((message.telegram_chat_id, reply_id))
            if target is not None:
                reply_to = {
                    "id": target.id,
                    "telegram_message_id": target.telegram_message_id,
                    "telegram_chat_id": target.telegram_chat_id,
                    "direction": target.direction,
                    "sender_type": target.sender_type,
                    "sender": sender_payload(
                        users.get(target.telegram_user_id), target.telegram_user_id
                    ),
                    "text": target.text,
                    "type": target.message_type,
                    "message_type": target.message_type,
                    "sticker": (
                        media_payload(target)
                        if target.message_type == "sticker"
                        else None
                    ),
                    "voice": (
                        media_payload(target)
                        if target.message_type == "voice"
                        else None
                    ),
                    "telegram_timestamp": (
                        target.telegram_timestamp.isoformat()
                        if target.telegram_timestamp
                        else None
                    ),
                    "created_at": (
                        target.created_at.isoformat() if target.created_at else None
                    ),
                }
        data["reply_to"] = reply_to
        serialized.append(data)

    return serialized

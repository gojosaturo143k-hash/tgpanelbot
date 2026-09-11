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

    # Phase 1 supports "text"; designed so "photo", "video" etc. fit later.
    message_type = Column(String(20), nullable=False, default="text", server_default="text")

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
        return {
            "id": self.id,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_message_id": self.telegram_message_id,
            "telegram_user_id": self.telegram_user_id,
            "direction": self.direction,
            "sender_type": self.sender_type,
            "text": self.text,
            "message_type": self.message_type,
            "reply_to_telegram_message_id": self.reply_to_telegram_message_id,
            "telegram_timestamp": (
                self.telegram_timestamp.isoformat() if self.telegram_timestamp else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

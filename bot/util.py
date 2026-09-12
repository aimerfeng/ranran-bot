from __future__ import annotations

import asyncio
import re

from telegram import Chat, Message, Update
from telegram.constants import ChatType, MessageEntityType

TELEGRAM_LIMIT = 4096
CHUNK_LIMIT = 4000
MAX_REPLY_CHARS = 12000

_SECRET_PATTERNS = (
    re.compile(r"\d{8,12}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[_-]?key|authorization)\s*[:=]\s*\S+"),
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def is_group(chat: Chat | None) -> bool:
    return bool(chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))


def is_private(chat: Chat | None) -> bool:
    return bool(chat and chat.type == ChatType.PRIVATE)


def command_args_text(update: Update, context_args: list[str] | None) -> str:
    if context_args:
        return " ".join(context_args).strip()
    message = update.effective_message
    if not message or not message.text:
        return ""
    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def sanitize_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    cleaned = _ANSI_RE.sub("", text or "")
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("***", cleaned)
    return cleaned


def split_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    body = text or ""
    truncated = False
    if len(body) > MAX_REPLY_CHARS:
        body = body[:MAX_REPLY_CHARS].rstrip()
        truncated = True
    if truncated:
        notice = "\n\n（输出过长，已截断）"
        keep = MAX_REPLY_CHARS - len(notice)
        body = body[:keep].rstrip() + notice

    if len(body) <= limit:
        return [body or "（空回复）"]

    chunks: list[str] = []
    remaining = body
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 3:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    return [c for c in chunks if c] or ["（空回复）"]


def display_name(user) -> str:
    if user is None:
        return "有人"
    return user.full_name or user.username or str(user.id)


def mentioned_bot(message: Message | None, bot_id: int, bot_username: str | None) -> bool:
    if message is None:
        return False
    text = message.text or message.caption or ""
    if not text:
        return False
    username = (bot_username or "").lstrip("@").lower()
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == MessageEntityType.MENTION and username:
            mention = text[entity.offset : entity.offset + entity.length]
            if mention.lstrip("@").lower() == username:
                return True
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            if entity.user.id == bot_id:
                return True
    if username and f"@{username}" in text.lower():
        return True
    return False


def strip_bot_mention(text: str, bot_username: str | None) -> str:
    username = (bot_username or "").lstrip("@")
    if not username:
        return (text or "").strip()
    cleaned = re.sub(rf"@{re.escape(username)}\b", "", text or "", flags=re.IGNORECASE)
    return cleaned.strip()


def should_auto_reply(text: str) -> bool:
    body = (text or "").strip()
    if len(body) < 4:
        return False
    if body.startswith("/"):
        return False
    if re.fullmatch(r"[\s.。,，!！?？~～…]+", body):
        return False
    return True


def chat_lock(locks: dict[int, asyncio.Lock], chat_id: int) -> asyncio.Lock:
    lock = locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[chat_id] = lock
    return lock

"""平台无关的文本处理：密钥脱敏与分条切分（供 Telegram / MCP 等适配器共用）。"""
from __future__ import annotations

import re

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


def sanitize_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    """去掉 ANSI 控制码，并把已知密钥/形似密钥的串打码。"""
    cleaned = _ANSI_RE.sub("", text or "")
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("***", cleaned)
    return cleaned


def split_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """按平台长度上限切分长文本，并在超长时截断。"""
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

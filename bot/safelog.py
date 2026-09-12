"""日志配置：统一入口，顺手把密钥从日志里抹掉。

httpx 的 INFO 日志会把请求 URL 完整打出来，而 Telegram Bot API 的 URL 里就带着
bot token（https://api.telegram.org/bot<id>:<token>/getUpdates）。默认 INFO 级别下，
每轮 getUpdates 都会往日志里写一份 token——日志一旦外泄就等于把 bot 交出去。
这里做两件事：把 httpx 降到 WARNING，并对所有日志记录做一次脱敏兜底。
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

# Telegram bot token：<bot id>:<35 位左右的字母数字串>
TELEGRAM_TOKEN = re.compile(r"\d{8,12}:[A-Za-z0-9_-]{30,}")
# 常见模型/服务密钥
API_KEYS = re.compile(r"\b(sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,})")
# 代理地址里的账号密码
PROXY_CRED = re.compile(r"(?i)\b(https?|socks5h?)://[^:/\s]+:[^@/\s]+@")

NOISY_LOGGERS = ("httpx", "httpcore", "telegram.request", "telegram.ext.Updater")


class RedactingFilter(logging.Filter):
    """兜底脱敏：任何 handler 上挂一个，保证写出去的日志里没有密钥。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # 格式化失败时不要因为脱敏再炸一次
            return True
        cleaned = scrub(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def scrub(text: str) -> str:
    """把文本里的 token / key / 代理凭据替换成占位符。"""
    if not text:
        return text
    text = TELEGRAM_TOKEN.sub("bot<TOKEN>", text)
    text = API_KEYS.sub("<KEY>", text)
    text = PROXY_CRED.sub(lambda m: m.group(1) + "://<CREDENTIALS>@", text)
    return text


def setup_logging(*, level: int = logging.INFO, stream: Any = None) -> None:
    """给 bot 与 MCP server 共用的日志初始化。"""
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=stream if stream is not None else sys.stderr,
    )
    redactor = RedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

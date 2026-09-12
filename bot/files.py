"""生成文件并发送：md / txt / csv 三种格式，投递层负责作为 Telegram 文档发出。"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from telegram import InputFile
from telegram.error import TelegramError

from bot.harness.types import Artifact

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (".md", ".txt", ".csv")
MAX_CHARS = 200_000
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class FileError(RuntimeError):
    pass


def sanitize_filename(raw: str, fmt: str = "", *, default_stem: str = "然然的文件") -> str:
    """洗掉路径分隔符和非法字符，并锁定扩展名白名单。"""
    wanted = (fmt or "").strip().lower().lstrip(".")
    name = " ".join(_UNSAFE.sub("", (raw or "").strip()).split()).strip(" .")
    if not name:
        name = default_stem
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = (stem.strip(" .") or default_stem)[:60]
    if wanted in {"md", "txt", "csv"}:
        return f"{stem}.{wanted}"
    tail = name.rpartition(".")[2].lower()
    extension = f".{tail}" if f".{tail}" in ALLOWED_EXTENSIONS else ".txt"
    return f"{stem}{extension}"


def normalize_csv(content: str) -> str:
    """按 RFC4180 重新序列化，保证 Excel/Numbers 能正常打开、引号转义合法。"""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text:
        raise FileError("CSV 内容是空的")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise FileError("CSV 解析后没有任何行")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def long_reply_filename(*, stem: str = "然然的回复", when: datetime | None = None) -> str:
    """长回复落成文件时用的名字，带时间戳避免重名。"""
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stem}-{stamp}.md"


OUTBOX_RETENTION_DAYS = 7


def prune_outbox(directory: Path, *, days: int = OUTBOX_RETENTION_DAYS, now: float | None = None) -> int:
    """清掉过期产物：这些文件已经发出去过，留着只会把磁盘吃满。"""
    directory = Path(directory)
    if days <= 0 or not directory.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - days * 86400
    removed = 0
    for item in directory.rglob("*"):
        try:
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Pruned %s expired files from %s", removed, directory)
    return removed


def create_artifact(directory: Path, *, filename: str, content: str, fmt: str = "") -> Artifact:
    """落盘一个产物文件；校验格式、大小与内容。"""
    text = content or ""
    if not text.strip():
        raise FileError("内容是空的，先把要写的内容准备好再调用。")
    if len(text) > MAX_CHARS:
        raise FileError(f"内容太长（{len(text)} 字符，上限 {MAX_CHARS}），请精简或拆成多个文件。")
    name = sanitize_filename(filename, fmt)
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = "/".join(ALLOWED_EXTENSIONS)
        raise FileError(f"只支持 {allowed}，收到 {extension or '无扩展名'}。")
    if extension == ".csv":
        text = normalize_csv(text)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    prune_outbox(directory)
    path = directory / f"{int(time.time())}-{name}"
    # 直接写字节：文本模式会把 CSV 的 \r\n 再转义成 \r\r\n，Excel 会看到空行。
    path.write_bytes(text.encode("utf-8-sig" if extension == ".csv" else "utf-8"))
    return Artifact(path=path, filename=name, size=path.stat().st_size)


async def send_artifact(message, artifact: Artifact, *, caption: str = ""):
    """把产物当作文档发给用户；失败只记日志，不影响已经发出的文字回复。

    成功时返回发出去的那条 Message（调用方可据此记住模型），失败返回 None。
    """
    try:
        with artifact.path.open("rb") as handle:
            return await message.reply_document(
                document=InputFile(handle, filename=artifact.filename),
                caption=caption or None,
            )
    except TelegramError as exc:
        logger.warning("Failed to send artifact %s: %s", artifact.filename, exc)
        return None
    except OSError as exc:
        logger.warning("Artifact unreadable %s: %s", artifact.path, exc)
        return None

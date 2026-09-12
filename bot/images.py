from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

from bot.sticker_commands import download_source

logger = logging.getLogger(__name__)

MAX_IMAGES = 4
IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime: str

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


def sniff_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def message_image_media(message: Any) -> Any | None:
    if message is None:
        return None
    photos = getattr(message, "photo", None)
    if photos:
        return photos[-1]
    document = getattr(message, "document", None)
    mime = (getattr(document, "mime_type", None) or "").split(";", 1)[0].strip().lower()
    if document is not None and mime in IMAGE_MIMES:
        return document
    sticker = getattr(message, "sticker", None)
    if sticker is not None and not getattr(sticker, "is_animated", False) and not getattr(sticker, "is_video", False):
        return sticker
    return None


async def download_image_part(bot: Any, media: Any) -> ImagePart | None:
    try:
        data = await download_source(bot, media)
    except ValueError:
        logger.info("Skipped image that failed download checks")
        return None
    except Exception:
        logger.warning("Image download failed")
        return None
    mime = sniff_image_mime(data)
    if mime is None:
        return None
    return ImagePart(data=data, mime=mime)


async def collect_images(bot: Any, *messages: Any) -> list[ImagePart]:
    images: list[ImagePart] = []
    seen: set[str] = set()
    for message in messages:
        media = message_image_media(message)
        if media is None:
            continue
        file_id = getattr(media, "file_id", None)
        if isinstance(file_id, str) and file_id in seen:
            continue
        part = await download_image_part(bot, media)
        if part is None:
            continue
        if isinstance(file_id, str):
            seen.add(file_id)
        images.append(part)
        if len(images) >= MAX_IMAGES:
            break
    return images

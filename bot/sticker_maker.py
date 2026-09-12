"""Deterministic local image rendering. No model or external image service."""
from __future__ import annotations

import io
import os
import warnings
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_STICKER_BYTES = 512 * 1024
MAX_CAPTION = 80
MAX_QUOTE = 500
RENDER_VERSION = '1'


@lru_cache(maxsize=32)
def load_font(size: int):
    candidates = [os.getenv('BOT_CJK_FONT', ''),
                  'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',
                  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                  '/System/Library/Fonts/PingFang.ttc']
    for name in candidates:
        if name and Path(name).is_file():
            return ImageFont.truetype(name, size=size)
    raise ValueError('缺少中文字体，请配置 BOT_CJK_FONT 为中文字体文件路径。')


def cjk_font_available() -> bool:
    """本机是否有可用中文字体（渲染贴纸/金句卡需要，测试据此决定是否跳过）。"""
    try:
        load_font(32)
    except (ValueError, OSError):
        return False
    return True


def wrap_text(text: str, font, width: int) -> list[str]:
    """Character wrapping preserves spaces, hard breaks, and the original words."""
    result = []
    for paragraph in text.split('\n'):
        line = ''
        for char in paragraph:
            if line and font.getlength(line + char) > width:
                result.append(line)
                line = ''
            line += char
        result.append(line)
    return result


def _decode(raw: bytes) -> Image.Image:
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('图片超过 10 MB，请先缩小图片。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise ValueError('请选择 PNG、JPG 或 WebP 静态图片；动态素材暂不转成单帧。')
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError('图片超过 2000 万像素，请先缩小图片。')
                if getattr(source, 'n_frames', 1) > 1:
                    raise ValueError('这是一张动态图，请换静态图片。')
                return ImageOps.exif_transpose(source).convert('RGBA')
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError('图片损坏或格式不支持，请换一张 PNG、JPG 或 WebP。') from exc


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = min(width / image.width, height / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _webp(image: Image.Image) -> bytes:
    for quality in (90, 80, 65, 45, 25):
        out = io.BytesIO()
        image.save(out, 'WEBP', quality=quality, method=4)
        if out.tell() <= MAX_STICKER_BYTES:
            return out.getvalue()
    raise ValueError('图片细节太多，压缩后仍超过贴纸大小限制，请换一张更简单的图。')


def make_sticker(raw: bytes, caption: str = '') -> bytes:
    caption = caption.strip()
    if len(caption) > MAX_CAPTION:
        raise ValueError('贴纸文字最多 80 字，请缩短一点。')
    image = _decode(raw)
    if not caption:
        return _webp(_fit(image, 512, 512))
    for size in range(42, 17, -2):
        font = load_font(size)
        lines = wrap_text(caption, font, 464)
        line_height = size + 12
        text_height = len(lines) * line_height + 24
        if text_height <= 228:
            break
    else:
        raise ValueError('文字换行太多，请减少文字或空行。')
    canvas = Image.new('RGBA', (512, 512))
    fitted = _fit(image, 512, 512 - text_height)
    canvas.alpha_composite(fitted, ((512 - fitted.width) // 2, (512 - text_height - fitted.height) // 2))
    draw = ImageDraw.Draw(canvas)
    y = 512 - text_height + 8
    for line in lines:
        draw.text((256, y), line, font=font, fill='white', stroke_width=2,
                  stroke_fill=(20, 24, 34, 255), anchor='mt')
        y += line_height
    return _webp(canvas)


def make_quote(text: str, author: str, message_id: int) -> bytes:
    if not text.strip():
        raise ValueError('请回复一条有文字或图片说明的消息，再发送 /quote。')
    if len(text) > MAX_QUOTE:
        raise ValueError('金句原文最多 500 字；请选一条短一点的消息，不会擅自删改原话。')
    for size in range(44, 23, -2):
        font = load_font(size)
        lines = wrap_text(text, font, 824)
        line_height = size + 22
        if len(lines) * line_height <= 1070:
            break
    else:
        raise ValueError('这条消息换行太多，请选一条更紧凑的消息。')
    height = max(520, 300 + len(lines) * line_height)
    canvas = Image.new('RGB', (1000, height), '#111827')
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((24,24,976,height-24),radius=26,fill='#1c2639',outline='#33425b',width=2)
    draw.rounded_rectangle((70,76,77,height-92),radius=3,fill='#a5b4fc')
    draw.text((105,65), '群 聊 金 句', font=load_font(22), fill='#a5b4fc')
    y = 139
    for line in lines:
        draw.text((105,y), line, font=font, fill='#f1f5f9', anchor='lt')
        y += line_height
    author = author.replace('\n',' ').replace('\r',' ')
    name_font = load_font(26)
    while name_font.getlength(author) > 770:
        author = author[:-2] + '…'
    draw.text((105,height-112), '— ' + author, font=name_font, fill='#cbd5e1', anchor='lt')
    draw.text((105,height-66), f'原文引用 · 消息 #{message_id}', font=load_font(18), fill='#8c9bb2', anchor='lt')
    out = io.BytesIO()
    canvas.save(out,'PNG',optimize=True)
    return out.getvalue()

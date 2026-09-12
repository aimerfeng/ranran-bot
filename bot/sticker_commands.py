"""Explicit opt-in fun commands; never add user media to the global sticker bank."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
import urllib.request
from collections import OrderedDict
from urllib.parse import urlparse

from telegram import InputFile, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.sticker_maker import MAX_INPUT_BYTES, RENDER_VERSION, make_quote, make_sticker
from bot.util import command_args_text, is_group, is_private

logger = logging.getLogger(__name__)


class FunMediaRuntime:
    def __init__(self):
        self.cache = OrderedDict()
        self.cooldowns = OrderedDict()
        self.active = 0

    def begin(self, key):
        now = time.monotonic()
        last = self.cooldowns.get(key, -100)
        if now - last < 5:
            raise ValueError('先等 5 秒再试，让我把上一条处理好。')
        if self.active >= 2:
            raise ValueError('正在处理其他图片，请过几秒再试。')
        self.cooldowns[key] = now
        self.cooldowns.move_to_end(key)
        while len(self.cooldowns) > 2048:
            self.cooldowns.popitem(last=False)

    async def run(self, key, work):
        self.begin(key)
        self.active += 1
        task = asyncio.create_task(work())
        def done(finished):
            self.active -= 1
            if not finished.cancelled():
                finished.exception()  # Retrieve errors even if the caller timed out.
        task.add_done_callback(done)
        try:
            await asyncio.wait_for(asyncio.shield(task), 60)
        except TimeoutError as exc:
            # Keep the slot occupied until the real operation exits.
            raise ValueError('处理时间较长，任务仍在结束处理中，请稍后查看结果再重试。') from exc

    def remember(self, key, file_id):
        self.cache[key] = file_id
        self.cache.move_to_end(key)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)


def _runtime(context):
    return context.bot_data.setdefault('fun_media', FunMediaRuntime())


def _allowed(update, context):
    chat, user = update.effective_chat, update.effective_user
    if chat is None or user is None or user.is_bot:
        return False
    if is_private(chat):
        return user.id == context.bot_data['settings'].owner_id
    return is_group(chat) and context.bot_data['whitelist'].contains(chat.id)


def _source(message):
    source = message.reply_to_message
    if source is None:
        raise ValueError('回复一张图片，再发送 /sticker；加字用 /sticker 我真的谢。')
    if getattr(source, 'animation', None) or getattr(source, 'video', None):
        raise ValueError('这条是动态素材，请换一张静态图片。')
    sticker = getattr(source, 'sticker', None)
    if sticker and (sticker.is_animated or sticker.is_video):
        raise ValueError('这张是动态贴纸，请换一张静态图片或静态贴纸。')
    photos = getattr(source, 'photo', None)
    media = photos[-1] if photos else (getattr(source, 'document', None) or sticker)
    if media is None:
        raise ValueError('回复一张 PNG、JPG、WebP 图片或静态贴纸，再发送 /sticker。')
    if (getattr(media, 'file_size', None) or 0) > MAX_INPUT_BYTES:
        raise ValueError('图片超过 10 MB，请先缩小图片。')
    return media


def _read_bounded(url: str) -> bytes:
    # Only Telegram's authenticated file endpoint; never fetch user-provided URLs.
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'api.telegram.org':
        raise ValueError('图片下载地址异常，请重新上传图片。')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(url, timeout=15) as response:
        if int(response.headers.get('Content-Length', '0')) > MAX_INPUT_BYTES:
            raise ValueError('图片超过 10 MB，请先缩小图片。')
        chunks, count, deadline = [], 0, time.monotonic() + 30
        while True:
            if time.monotonic() > deadline:
                raise ValueError('图片下载超时，请稍后再试。')
            part = response.read(min(65536, MAX_INPUT_BYTES + 1 - count))
            if not part:
                break
            chunks.append(part); count += len(part)
            if count > MAX_INPUT_BYTES:
                raise ValueError('图片超过 10 MB，请先缩小图片。')
        return b''.join(chunks)


async def download_source(bot, media):
    file = await bot.get_file(media.file_id, read_timeout=20, connect_timeout=10)
    if (file.file_size or 0) > MAX_INPUT_BYTES:
        raise ValueError('图片超过 10 MB，请先缩小图片。')
    if not file.file_path:
        raise ValueError('图片文件暂时不可用，请重新上传图片。')
    return await asyncio.to_thread(_read_bounded, file.file_path)


def _args(update, context):
    # Preserve deliberate line breaks instead of joining context.args.
    text = update.effective_message.text or ''
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else command_args_text(update, context.args)


async def _execute(update, context, work):
    try:
        await _runtime(context).run((update.effective_chat.id, update.effective_user.id), work)
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
    except TelegramError:
        logger.warning('Fun command Telegram transfer failed')
        await update.effective_message.reply_text('贴纸或卡片发送失败，请稍后再试。')
    except Exception:
        # Do not log exception URLs: Telegram download URLs contain the bot token.
        logger.warning('Fun command rendering or download failed')
        await update.effective_message.reply_text('图片处理失败，请换一张图或稍后再试。')


async def _ensure_group_pack(update, context, file_id: str) -> None:
    try:
        packs = context.bot_data['group_packs']
        title = update.effective_chat.title or '群聊表情包'
        pack = await packs.add(
            context.bot, update.effective_chat.id, title,
            context.bot_data['settings'].owner_id, file_id, '🙂'
        )
        logger.info('Group sticker pack ready: chat=%s name=%s title=%s count=%s',
                    update.effective_chat.id, pack.name, pack.title, len(pack.ids))
        await update.effective_message.reply_text(
            f'已加入群贴纸包「{pack.title}」：https://t.me/addstickers/{pack.name}'
        )
    except TelegramError as exc:
        logger.exception('Group sticker pack update failed: %s', exc)
        # The sticker itself was already delivered; explain the separate pack failure.
        await update.effective_message.reply_text(
            '贴纸已经做好了，但加入群贴纸包失败：%s' % str(exc)[:240]
        )


async def sticker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update, context) or update.effective_message is None:
        return
    message = update.effective_message
    try:
        media = _source(message)
        caption = _args(update, context)
        if len(caption) > 80:
            raise ValueError('贴纸文字最多 80 字，请缩短一点。')
    except ValueError as exc:
        await message.reply_text(str(exc)); return
    runtime = _runtime(context)
    identity = getattr(media, 'file_unique_id', None) or media.file_id
    key = (update.effective_chat.id, identity, hashlib.sha256(caption.encode()).hexdigest(), RENDER_VERSION)
    async def work():
        cached = runtime.cache.get(key)
        if cached:
            try:
                sent = await message.reply_sticker(cached, read_timeout=20, write_timeout=30)
                runtime.cache.move_to_end(key)
                if sent.sticker and is_group(update.effective_chat):
                    await _ensure_group_pack(update, context, sent.sticker.file_id)
                return
            except TelegramError:
                runtime.cache.pop(key, None)
        raw = await download_source(context.bot, media)
        rendered = await asyncio.to_thread(make_sticker, raw, caption)
        sent = await message.reply_sticker(InputFile(rendered, filename='sticker.webp'), read_timeout=20, write_timeout=30)
        if sent.sticker:
            runtime.remember(key, sent.sticker.file_id)
            if is_group(update.effective_chat):
                await _ensure_group_pack(update, context, sent.sticker.file_id)
    await _execute(update, context, work)


def _author(source):
    origin = getattr(source, 'forward_origin', None)
    if origin:
        user = getattr(origin, 'sender_user', None)
        chat = getattr(origin, 'chat', None) or getattr(origin, 'sender_chat', None)
        return ((user.full_name if user else None) or getattr(origin, 'sender_user_name', None)
                or (chat.title if chat else None) or '转发消息')
    chat = getattr(source, 'sender_chat', None)
    user = getattr(source, 'from_user', None)
    return (chat.title if chat else None) or (user.full_name if user else None) or '群友'


async def quote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update, context) or update.effective_message is None:
        return
    message = update.effective_message
    source = message.reply_to_message
    text = (getattr(source, 'text', None) or getattr(source, 'caption', None)) if source else None
    if not text or _args(update, context):
        await message.reply_text('回复一条文字消息或图片说明，再发送 /quote（不带额外文字，保留原话）。')
        return
    async def work():
        rendered = await asyncio.to_thread(make_quote, text, _author(source), source.message_id)
        await message.reply_photo(rendered, filename='quote.png', read_timeout=20, write_timeout=30)
    await _execute(update, context, work)


async def choose_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update, context) or update.effective_message is None:
        return
    raw = _args(update, context)
    choices = [s.strip() for s in raw.split('|')] if '|' in raw else raw.split()
    if any(not s for s in choices):
        choices = []
    choices = list(dict.fromkeys(choices))
    if not 2 <= len(choices) <= 20 or any(len(s) > 100 for s in choices):
        await update.effective_message.reply_text('至少给我 2 个不同选项，最多 20 个，每项最多 100 字。\n/choose 火锅 烤肉 拉面\n选项含空格时：/choose 出去吃饭 | 在家 做饭')
        return
    await update.effective_message.reply_text('我选：' + secrets.choice(choices))

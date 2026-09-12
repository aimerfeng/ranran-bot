from __future__ import annotations

import logging
import random
from io import BytesIO

import httpx
from telegram import Message
from telegram.constants import ReactionEmoji
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

_HTTP_HEADERS = {
    "User-Agent": "tg-llm-bot/1.0",
    "Accept": "application/json",
}

_REACTION_HINTS = (
    (ReactionEmoji.ROLLING_ON_THE_FLOOR_LAUGHING, "哈哈 草 笑死 www 乐"),
    (ReactionEmoji.CRYING_FACE, "呜 哭 难过 惨"),
    (ReactionEmoji.POUTING_FACE, "滚 骂 气 哼"),
    (ReactionEmoji.SMILING_FACE_WITH_HEARTS, "爱 喜欢 亲 贴贴 好看"),
    (ReactionEmoji.FACE_THROWING_A_KISS, "亲 么么"),
    (ReactionEmoji.THINKING_FACE, "为什么 怎么 哈？"),
    (ReactionEmoji.FIRE, "牛 强 帅 冲"),
    (ReactionEmoji.THUMBS_UP, "好 行 可以 ok"),
    (ReactionEmoji.EYES, "哦 嗯 看"),
    (ReactionEmoji.SLEEPING_FACE, "困 睡"),
    (ReactionEmoji.BROKEN_HEART, "分手 心碎"),
    (ReactionEmoji.KISS_MARK, "色 涩"),
)

_DEFAULT_REACTIONS = (
    ReactionEmoji.THUMBS_UP,
    ReactionEmoji.RED_HEART,
    ReactionEmoji.FIRE,
    ReactionEmoji.THINKING_FACE,
    ReactionEmoji.EYES,
    ReactionEmoji.GRINNING_FACE_WITH_SMILING_EYES,
)

_WELCOME = (
    "{name} 来了？……行吧，先找个角落坐下。",
    "新面孔。{name}，群规就一条：别把本小姐当客服。",
    "{name} 进来了。打招呼可以，套近乎先排队。",
    "哦，{name}。茶在那边，脑子带好。",
)

_FORTUNE_BANDS = (
    (92, "大吉。今天抽卡、告白、抬杠都可以，宇宙站你这边。"),
    (80, "中吉。手感在线，想做的事趁现在，别等本小姐催你。"),
    (65, "小吉。平稳得有点无聊，去群里找个人互损一下。"),
    (50, "末吉。别作死就行，作死的话……也不是不能看戏。"),
    (35, "小凶。出门记得看路，抬杠记得看对象。"),
    (18, "凶。今天适合躺着，适合被我骂，不适合作大死。"),
    (0, "大凶。把手机放下。不行的话来找我，至少骂完会清醒一点。"),
)


def infer_reaction(text: str) -> str:
    body = text or ""
    for emoji, hints in _REACTION_HINTS:
        if any(token in body for token in hints.split()):
            return emoji
    return random.choice(_DEFAULT_REACTIONS)


async def react_to(message: Message, text: str, *, big: bool = False) -> bool:
    try:
        await message.set_reaction(infer_reaction(text), is_big=big)
        return True
    except TelegramError:
        return False


def welcome_line(name: str) -> str:
    return random.choice(_WELCOME).format(name=name)


async def _json(client: httpx.AsyncClient, url: str) -> dict:
    response = await client.get(url, headers=_HTTP_HEADERS, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("图源返回了奇怪的东西。")
    return data


async def _from_lolicon(client: httpx.AsyncClient, *, r18: int) -> str:
    data = await _json(client, f"https://api.lolicon.app/setu/v2?r18={r18}&size=regular&num=1")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError("没抽到图")
    url = (rows[0].get("urls") or {}).get("regular")
    if not url:
        raise RuntimeError("没抽到图")
    return str(url)


async def _from_nekobot(client: httpx.AsyncClient, kind: str) -> str:
    data = await _json(client, f"https://nekobot.xyz/api/image?type={kind}")
    url = data.get("message")
    if not url:
        raise RuntimeError("没抽到图")
    return str(url)


async def _from_nekos_best(client: httpx.AsyncClient) -> str:
    data = await _json(client, "https://nekos.best/api/v2/waifu")
    rows = data.get("results") or []
    url = rows[0].get("url") if rows else None
    if not url:
        raise RuntimeError("没抽到图")
    return str(url)


async def _from_nekos_life(client: httpx.AsyncClient) -> str:
    data = await _json(client, "https://nekos.life/api/v2/img/waifu")
    url = data.get("url")
    if not url:
        raise RuntimeError("没抽到图")
    return str(url)


async def fetch_anime_image(*, nsfw: bool = False) -> str:
    sources = (
        (
            lambda c: _from_lolicon(c, r18=1),
            lambda c: _from_nekobot(c, "hentai"),
            lambda c: _from_nekobot(c, "neko"),
        )
        if nsfw
        else (
            _from_nekos_best,
            _from_nekos_life,
            lambda c: _from_lolicon(c, r18=0),
        )
    )
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for fetch in sources:
            try:
                return await fetch(client)
            except Exception as exc:
                last_error = exc
                logger.info("Anime source failed: %s", exc)
    raise RuntimeError("图源现在抽风了。") from last_error


async def download_image(url: str) -> tuple[BytesIO, str]:
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "tg-llm-bot/1.0"})
    response.raise_for_status()
    payload = response.content
    if len(payload) < 100:
        raise RuntimeError("图源给了空文件。")
    name = "image.gif" if payload[:6] in {b"GIF87a", b"GIF89a"} else "image.jpg"
    if payload[:8] == b"\x89PNG\r\n\x1a\n":
        name = "image.png"
    buf = BytesIO(payload)
    buf.name = name
    buf.seek(0)
    return buf, name


async def send_anime_image(message: Message, *, nsfw: bool) -> None:
    url = await fetch_anime_image(nsfw=nsfw)
    caption = "看完别装正经。" if nsfw else "给你。看完记得说话。"
    try:
        await message.reply_photo(url, caption=caption)
        return
    except TelegramError as exc:
        logger.info("reply_photo by url failed, uploading bytes: %s", exc)
    buf, name = await download_image(url)
    if name.endswith(".gif"):
        await message.reply_animation(buf, caption=caption)
        return
    await message.reply_photo(buf, caption=caption)

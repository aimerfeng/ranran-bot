from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

from telegram import Bot, Sticker
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

TARGET_COUNT = 59
DEFAULT_SETS = ("Kokomi", "MarinKitagawa")

MOODS = ("笑", "哭", "怒", "羞", "爱", "嫌", "困", "惊", "嗨", "无语", "贴贴", "问")

_MOOD_ALIASES = {
    "笑": "笑",
    "哈哈": "笑",
    "乐": "笑",
    "草": "笑",
    "哭": "哭",
    "呜": "哭",
    "泪": "哭",
    "怒": "怒",
    "骂": "怒",
    "气": "怒",
    "羞": "羞",
    "脸红": "羞",
    "爱": "爱",
    "亲": "爱",
    "色": "爱",
    "喜欢": "爱",
    "嫌": "嫌",
    "白眼": "嫌",
    "切": "嫌",
    "困": "困",
    "睡": "困",
    "惊": "惊",
    "啊": "惊",
    "嗨": "嗨",
    "兴奋": "嗨",
    "无语": "无语",
    "嗯": "无语",
    "贴贴": "贴贴",
    "摸": "贴贴",
    "问": "问",
    "疑惑": "问",
}

_EMOJI_MOOD = {
    "笑": "😂🤣😆😄😊😁😃😉😋😀🙂😌😎🤪😜😛🤭",
    "哭": "😢😭😞🥺😥😓😔😩😖😫💔",
    "怒": "😡😠🤬😤👿😈",
    "羞": "😳☺️🥺🥲",
    "爱": "😍🥰😘❤️❤💕💋💘😻",
    "嫌": "🙄😒😑😏🙃🤨😐😶",
    "困": "😴🥱😪",
    "惊": "😱😮😲🤯😨😰😵",
    "嗨": "🎉🥳🤩🔥✨💯👍👏",
    "无语": "😐😶😑🫤💭",
    "贴贴": "🤗🫂🤝",
    "问": "🤔❓😕🧐",
}

_TAG_RE = re.compile(r"\[表情[:：]\s*([^\]]+)\]")
_MOOD_HINTS = (
    ("笑", "哈哈 草 笑死 乐 哈哈哈 www"),
    ("哭", "呜 哭 泪 难过"),
    ("怒", "滚 骂 气死 哼"),
    ("羞", "才不是 脸红 不要啦"),
    ("爱", "亲 喜欢 爱 贴贴 过来"),
    ("嫌", "切 白眼 无语 哈？"),
    ("困", "困 睡 哈欠"),
    ("惊", "啊？ 啥 等下"),
    ("嗨", "好耶 可以 冲"),
    ("问", "哈？ 欸 为什么"),
    ("贴贴", "贴贴 摸摸 抱"),
)


@dataclass(frozen=True)
class StickerItem:
    file_id: str
    unique_id: str
    emoji: str
    mood: str
    set_name: str


def normalize_mood(raw: str | None) -> str | None:
    key = (raw or "").strip().lower()
    if not key:
        return None
    if key in MOODS:
        return key
    return _MOOD_ALIASES.get(key)


def mood_from_emoji(emoji: str | None) -> str:
    mark = emoji or ""
    for mood, chars in _EMOJI_MOOD.items():
        if any(ch in mark for ch in chars):
            return mood
    return "嗨"


def infer_mood(text: str) -> str | None:
    body = text or ""
    for mood, hints in _MOOD_HINTS:
        if any(token in body for token in hints.split()):
            return mood
    return None


def split_sticker_tag(text: str) -> tuple[str | None, str]:
    mood: str | None = None

    def _keep(match: re.Match[str]) -> str:
        nonlocal mood
        mood = normalize_mood(match.group(1)) or mood
        return ""

    cleaned = _TAG_RE.sub(_keep, text or "").strip()
    return mood, cleaned


def should_attach_sticker(trigger: str) -> bool:
    chance = {
        "主动接话": 0.55,
        "@提到你": 0.35,
        "回复你的消息": 0.35,
        "私聊": 0.25,
        "有人丢表情": 0.7,
    }.get(trigger, 0.15)
    return random.random() < chance


class StickerBank:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, StickerItem] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read sticker bank: %s", exc)
            return
        for row in raw.get("stickers") or []:
            item = self._from_row(row)
            if item:
                self._items[item.unique_id] = item

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stickers": [
                {
                    "file_id": item.file_id,
                    "unique_id": item.unique_id,
                    "emoji": item.emoji,
                    "mood": item.mood,
                    "set_name": item.set_name,
                }
                for item in self._items.values()
            ]
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _from_row(row: object) -> StickerItem | None:
        if not isinstance(row, dict):
            return None
        file_id = str(row.get("file_id") or "")
        unique_id = str(row.get("unique_id") or "")
        if not file_id or not unique_id:
            return None
        return StickerItem(
            file_id=file_id,
            unique_id=unique_id,
            emoji=str(row.get("emoji") or ""),
            mood=str(row.get("mood") or "嗨"),
            set_name=str(row.get("set_name") or ""),
        )

    def count(self) -> int:
        return len(self._items)

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for item in self._items.values():
            counts[item.mood] = counts.get(item.mood, 0) + 1
        if not counts:
            return "还没有表情包。私聊丢贴纸给我，或用 /stickerset 导入一套。"
        parts = " ".join(f"{mood}{counts.get(mood, 0)}" for mood in MOODS if counts.get(mood))
        extra = sum(n for mood, n in counts.items() if mood not in MOODS)
        if extra:
            parts += f" 其他{extra}"
        return f"现在有 {self.count()} 张表情：{parts}"

    def add_sticker(self, sticker: Sticker, mood: str | None = None, *, persist: bool = True) -> bool:
        item = StickerItem(
            file_id=sticker.file_id,
            unique_id=sticker.file_unique_id,
            emoji=sticker.emoji or "",
            mood=mood or mood_from_emoji(sticker.emoji),
            set_name=sticker.set_name or "",
        )
        existed = item.unique_id in self._items
        self._items[item.unique_id] = item
        if persist:
            self._save()
        return not existed

    def pick(self, mood: str | None = None) -> StickerItem | None:
        if not self._items:
            return None
        pool = [item for item in self._items.values() if not mood or item.mood == mood]
        if not pool:
            pool = list(self._items.values())
        return random.choice(pool)

    async def import_set(self, bot: Bot, name: str, *, cap: int | None = None) -> int:
        set_name = name.strip().removeprefix("https://t.me/addstickers/")
        set_name = set_name.removeprefix("t.me/addstickers/")
        if not set_name:
            raise ValueError("贴纸包名字是空的。")
        try:
            pack = await bot.get_sticker_set(set_name)
        except TelegramError as exc:
            raise ValueError(f"找不到贴纸包 {set_name}。") from exc
        added = 0
        for sticker in pack.stickers:
            if cap is not None and self.count() >= cap:
                break
            if sticker.file_unique_id in self._items:
                continue
            if self.add_sticker(sticker, persist=False):
                added += 1
        if added:
            self._save()
        return added

    async def ensure_defaults(self, bot: Bot) -> None:
        if self.count() >= TARGET_COUNT:
            return
        for name in DEFAULT_SETS:
            if self.count() >= TARGET_COUNT:
                break
            try:
                added = await self.import_set(bot, name, cap=TARGET_COUNT)
                logger.info("Imported sticker set %s (+%s, total=%s)", name, added, self.count())
            except ValueError as exc:
                logger.warning("Default sticker set skipped: %s", exc)
        self._save()

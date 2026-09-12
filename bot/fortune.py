"""Local, deterministic daily fortune cards for Telegram; no remote calls at runtime."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import unicodedata
from collections import OrderedDict
from threading import Lock
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from telegram.error import TelegramError

from bot.fortune_poetry import draw_verse, poetry_catalog, verse_columns

logger = logging.getLogger(__name__)
ASSETS = Path(__file__).parent / 'assets' / 'fortune'
SHANGHAI = timezone(timedelta(hours=8))
THEMES = {
    'genshin': '原神', 'arknights': '明日方舟', 'pcr': '公主连结', 'touhou': '东方',
    'azure': '碧蓝航线', 'pretty_derby': '赛马娘', 'punishing': '战双帕弥什',
    'granblue_fantasy': '碧蓝幻想', 'touhou_lostword': '东方归言录',
    'hololive': 'Hololive', 'onmyoji': '阴阳师', 'warship_girls_r': '战舰少女R',
}
THEME_ALIASES = {**{k: k for k in THEMES}, **{v.lower(): k for k, v in THEMES.items()},
                 '方舟': 'arknights', '公主链接': 'pcr', '碧蓝': 'azure', '马娘': 'pretty_derby',
                 '战双': 'punishing', '舰r': 'warship_girls_r', '舰R': 'warship_girls_r',
                 'holo': 'hololive', '随机': 'random', 'random': 'random', '': 'random'}
THEME_HELP = ('二次元每日签 · 12 套主题\n/yun — 每次随机主题抽签 + AI 解签\n'
              + '\n'.join('/yun ' + label for label in THEMES.values())
              + '\n有机会邂逅稀有隐藏签。\n连续随机抽签不重复上一主题；同一天切换主题只换签底，不重抽吉凶和隐藏签。北京时间 00:00 更新。')
BANDS = ((92, '大吉'), (80, '中吉'), (65, '小吉'), (50, '末吉'), (35, '小凶'), (18, '凶'), (0, '大凶'))
COLORS = (('琥珀金', '#d9b66f'), ('月光银', '#c4d0e4'), ('雾霭蓝', '#85b4cf'), ('鼠尾草绿', '#9cbea7'), ('烟霞紫', '#b3a1d4'), ('玫瑰粉', '#d99dae'))
ACTIVITIES = ('整理桌面', '读几页书', '散步放空', '主动问候', '记录灵感', '早点休息', '完成小事', '认真倾听', '练习技能', '整理相册', '给自己留白', '听一首歌')
AVOID = ('冲动消费', '熬夜刷屏', '无效争论', '拖延计划', '过度比较', '三心二意', '自我内耗', '敷衍回应', '仓促承诺', '忘记喝水')

@dataclass(frozen=True)
class HiddenSign:
    key: str
    title: str
    rarity: str
    weight: int  # Out of 10,000 equally likely tickets.
    advice: str
    color: str
    imagery: str = ""


HIDDEN_SIGNS = (
    HiddenSign('starlight', '星遇', 'SR', 100, '星落平湖开万顷，月移远岫见千重', '#b4cfff', '月'),
    HiddenSign('cat', '猫缘', 'SR', 100, '狸奴卧在花阴里，春日停于小院中', '#f5bfcc', '花'),
    HiddenSign('sakura', '樱约', 'SR', 100, '樱枝有约随风信，花雨无声落故衣', '#ffb7d5', '花'),
    HiddenSign('moon', '月守', 'SR', 100, '月守疏窗灯守夜，风穿小院梦穿云', '#c6baff', '月'),
    HiddenSign('reverse', '逆转', 'SSR', 40, '水到回湾舟转向，云开旧岭路重生', '#ffe093', '水'),
    HiddenSign('destiny', '天选', 'SSR', 40, '山川不语留君位，风月相逢认旧人', '#ffe093', '山'),
    HiddenSign('miracle', '奇迹', 'UR', 15, '石隙忽生三寸绿，云深乍见一痕晴', '#8ce9e2', '山'),
    HiddenSign('blank', '空白', 'SP', 5, '水净无痕容落笔，山空有色待题诗', '#fff0c1', '水'),
)


def hidden_from_ticket(ticket: int) -> HiddenSign | None:
    if not 0 <= ticket < 10000:
        raise ValueError('Hidden ticket must be in [0, 10000)')
    cumulative = 0
    for sign in HIDDEN_SIGNS:
        cumulative += sign.weight
        if ticket < cumulative:
            return sign
    return None


def daily_hidden(user_id: int, day: date) -> HiddenSign | None:
    # Independent seed leaves the ordinary fortune and artwork unchanged.
    rng = random.Random(hashlib.sha256(f'yun-hidden-v1:{user_id}:{day.isoformat()}'.encode()).digest())
    sign = hidden_from_ticket(rng.randrange(10000))
    if sign is None:
        return None
    poem = draw_verse(user_id, day, hidden_key=sign.key)
    return replace(sign, advice=poem.verse, imagery=poem.imagery)


@dataclass(frozen=True)
class Fortune:
    name: str
    day: date
    score: int
    band: str
    advice: str
    metrics: tuple[tuple[str, int], ...]
    good: tuple[str, ...]
    avoid: tuple[str, ...]
    color: str
    color_hex: str
    number: int
    direction: str
    item: str
    theme: str
    image_key: str
    hidden: HiddenSign | None = None
    poetry_imagery: str = ""

    @property
    def display_imagery(self) -> str:
        return self.hidden.imagery if self.hidden else self.poetry_imagery

    @property
    def display_title(self) -> str:
        return self.hidden.title if self.hidden else self.band

    @property
    def display_advice(self) -> str:
        return self.hidden.advice if self.hidden else self.advice

    @property
    def result_label(self) -> str:
        if self.hidden:
            return f'隐藏签 · {self.hidden.rarity} · {self.hidden.title}'
        return self.band

    def text(self) -> str:
        return (f'{self.name} · 今日运势\n{self.day:%Y-%m-%d}（北京时间）\n'
                f'{self.result_label} · {self.display_imagery}意\n{self.display_advice}\n基础运势：{self.band} · {self.score}/100\n\n'
                + ' / '.join(f'{label} {score}' for label, score in self.metrics)
                + f'\n宜：{" · ".join(self.good)}\n忌：{" · ".join(self.avoid)}'
                + f'\n幸运色：{self.color} · 幸运数字：{self.number}\n幸运方位：{self.direction} · 幸运物：{self.item}'
                + '\n\n仅供娱乐，不是现实预测。每天 00:00（北京时间）更新。')


def shanghai_day(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(SHANGHAI).date()


@lru_cache(maxsize=1)
def copywriting() -> dict[str, tuple[str, ...]]:
    data = json.loads((ASSETS / 'copywriting.json').read_text(encoding='utf-8'))
    return {row['good-luck']: tuple(row['content']) for row in data['copywriting']}


@lru_cache(maxsize=1)
def theme_images() -> dict[str, tuple[str, ...]]:
    data = json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))
    result = {}
    for theme in THEMES:
        paths = tuple(sorted(r['path'] for r in data['files'] if r['path'].startswith(f'img/{theme}/')))
        if not paths or any('..' in Path(p).parts for p in paths):
            raise ValueError(f'Invalid fortune image manifest: {theme}')
        result[theme] = paths
    return result


# Only presentation history is volatile; daily luck never depends on this cache.
_recent_themes: OrderedDict[tuple[int, date], str] = OrderedDict()
_theme_lock = Lock()


def _random_theme(user_id: int, day: date) -> str:
    key = (user_id, day)
    with _theme_lock:
        previous = _recent_themes.get(key)
        selected = random.choice(tuple(t for t in THEMES if t != previous))
        _recent_themes[key] = selected
        _recent_themes.move_to_end(key)
        if len(_recent_themes) > 10000:
            _recent_themes.popitem(last=False)
        return selected


def build_fortune(user_id: int, name: str, day: date | None = None, *, theme: str = 'random') -> Fortune:
    day = day or shanghai_day()
    if theme not in (*THEMES, 'random'):
        raise ValueError('Unknown fortune theme')
    # Randomize the theme per request, independently of the daily fortune seed.
    if theme == 'random':
        theme = _random_theme(user_id, day)
    image_rng = random.Random(hashlib.sha256(f'yun-art-v1:{user_id}:{day.isoformat()}:{theme}'.encode()).digest())
    image_key = image_rng.choice(theme_images()[theme])
    rng = random.Random(hashlib.sha256(f'yun-v2:{user_id}:{day.isoformat()}'.encode()).digest())
    score = rng.randint(1, 100)
    band = next(label for floor, label in BANDS if score >= floor)
    # Retain the old RNG draw so upgrading verses does not reroll metrics/lucky hints.
    rng.choice(copywriting()[band])
    poem = draw_verse(user_id, day, band=band)
    advice = poem.verse
    metrics = tuple((label, max(1, min(100, score + rng.randint(-22, 22))))
                    for label in ('事业', '学业', '财运', '感情', '人际', '活力'))
    color, hex_color = rng.choice(COLORS)
    clean_name = ''.join(c for c in name if not unicodedata.category(c).startswith('C')).strip()[:80] or '旅人'
    return Fortune(clean_name, day, score, band, advice, metrics,
                   tuple(rng.sample(ACTIVITIES, 2)), tuple(rng.sample(AVOID, 2)),
                   color, hex_color, rng.randint(0, 9), rng.choice(('东', '南', '西', '北', '东南', '西南', '东北', '西北')),
                   rng.choice(('耳机', '笔记本', '帆布包', '书签', '水杯', '钢笔', '手帕', '钥匙扣')), theme, image_key, daily_hidden(user_id, day), poem.imagery)


def _font_path(bold: bool = False) -> str:
    candidates = (os.environ.get('YUN_FONT_PATH', ''), str(ASSETS / 'font.ttf'),
                  'C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc',
                  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                  '/System/Library/Fonts/PingFang.ttc')
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise OSError('Set YUN_FONT_PATH to a Chinese TrueType/OpenType font')


def render_card(f: Fortune) -> bytes:
    """Render on the upstream anime slip, keeping the character art untouched.

    Layout follows nonebot_plugin_fortune/utils.py (MIT, KafCoppelia).
    Uses Pillow's modern anchors instead of removed ImageFont.getsize().
    """
    from PIL import Image, ImageDraw, ImageFont
    import math

    with Image.open(ASSETS / f.image_key) as source:
        im = source.convert('RGB').resize((960, 960), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(im)
    title_font = ImageFont.truetype(str(ASSETS / 'font' / 'Mamelon.otf'), 90)
    body_font = ImageFont.truetype(str(ASSETS / 'font' / 'sakura.ttf'), 50)
    draw.text((280, 198), f.display_title, font=title_font, fill='#F5F5F5', anchor='mm')

    # Two complete seven-character clauses: first on the right, second on the left.
    poetic_columns = verse_columns(f.display_advice)
    if poetic_columns is not None:
        pieces = list(poetic_columns)
    else:
        # Compatibility for an externally supplied or legacy non-poetic sample.
        text = ''.join(f.display_advice.split())
        columns = max(1, math.ceil(len(text) / 9))
        if columns > 4:
            raise ValueError('Fortune text exceeds the signing area')
        rows = math.ceil(len(text) / columns)
        pieces = [text[i:i + rows] for i in range(0, len(text), rows)]
    pitch = 58
    for column, piece in enumerate(pieces):
        x = 280 + (len(pieces) - 1) * pitch / 2 - column * pitch
        for row, glyph in enumerate(piece):
            y = 594 - (len(piece) - 1) * pitch / 2 + row * pitch
            draw.text((x, y), glyph, font=body_font, fill='#323232', anchor='mm')
    if f.hidden:
        # Badge stays on the left signing area, leaving the character untouched.
        color = f.hidden.color
        badge_font = ImageFont.truetype(_font_path(True), 23)
        draw.rounded_rectangle((116, 820, 444, 906), radius=16,
                               fill='#202334', outline=color, width=3)
        draw.text((280, 845), f'{f.hidden.rarity} · 隐藏签',
                  font=badge_font, fill=color, anchor='mm')
        draw.text((280, 880), '今日限定相遇', font=badge_font, fill='#f7f3eb', anchor='mm')
    out = BytesIO()
    im.save(out, format='PNG', optimize=True)
    return out.getvalue()


# Limit parallel rendering so a group burst cannot exhaust rendering threads.
_render_slots = asyncio.Semaphore(2)

async def send_fortune(message, user_id: int, name: str, *, theme: str = 'random') -> Fortune:
    f = build_fortune(user_id, name, theme=theme)
    try:
        async with _render_slots:
            image = await asyncio.to_thread(render_card, f)
        photo = BytesIO(image)
        photo.name = f'yun-{f.day.isoformat()}.png'
        await message.reply_photo(photo=photo, caption=f'{f.name} · {THEMES[f.theme]}签 · {f.result_label}\n山水花月 · {f.display_imagery}意\n{f.display_advice}\n{f.day:%Y-%m-%d} · 同日吉凶不变 · 仅供娱乐\n/yun 主题 查看其他签底', parse_mode=None)
    except (TelegramError, OSError, ValueError):
        logger.warning('Fortune photo failed; sending full text', exc_info=True)
        await message.reply_text(f.text(), parse_mode=None)
    return f

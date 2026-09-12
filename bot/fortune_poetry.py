"""Curated original landscape verses; independent selection preserves luck RNG."""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

POETRY_PATH = Path(__file__).parent / 'assets' / 'fortune' / 'poetry.json'
VERSE_RE = re.compile(r'^([\u4e00-\u9fff]{7})，([\u4e00-\u9fff]{7})$')


@dataclass(frozen=True)
class Verse:
    verse: str
    imagery: str


def verse_columns(text: str) -> tuple[str, str] | None:
    match = VERSE_RE.fullmatch(text)
    return (match.group(1), match.group(2)) if match else None


@lru_cache(maxsize=1)
def poetry_catalog() -> dict:
    data = json.loads(POETRY_PATH.read_text(encoding='utf-8'))
    seen = set()
    for section in ('ordinary', 'hidden'):
        for key, rows in data[section].items():
            if not rows:
                raise ValueError(f'Empty poetry pool: {key}')
            for row in rows:
                if row['imagery'] not in ('山', '水', '花', '月') or not verse_columns(row['verse']):
                    raise ValueError(f'Invalid poetry entry: {key}')
                if row['verse'] in seen:
                    raise ValueError(f'Duplicate poetry entry: {key}')
                seen.add(row['verse'])
    return data


def draw_verse(user_id: int, day: date, *, band: str | None = None,
               hidden_key: str | None = None) -> Verse:
    if (band is None) == (hidden_key is None):
        raise ValueError('Choose exactly one ordinary band or hidden key')
    section, key = ('hidden', hidden_key) if hidden_key is not None else ('ordinary', band)
    rows = poetry_catalog()[section][key]
    seed = hashlib.sha256(f'yun-poetry-v1:{user_id}:{day.isoformat()}:{section}:{key}'.encode()).digest()
    row = random.Random(seed).choice(rows)
    return Verse(row['verse'], row['imagery'])

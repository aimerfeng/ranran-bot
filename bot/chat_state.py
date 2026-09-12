from __future__ import annotations

import json
import random
import time
from collections import defaultdict, deque
from pathlib import Path

from bot.models import DEFAULT_MODEL_KEY, resolve_model_key


class ChatStateStore:
    def __init__(self, path: Path, default_model: str = DEFAULT_MODEL_KEY) -> None:
        self.path = path
        self.default_model = resolve_model_key(default_model)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            self._data = raw

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def _entry(self, chat_id: int) -> dict:
        key = str(chat_id)
        if key not in self._data or not isinstance(self._data[key], dict):
            self._data[key] = {
                "model": self.default_model,
                "auto_reply": True,
                "nsfw": False,
            }
        return self._data[key]

    def get_model(self, chat_id: int) -> str:
        return resolve_model_key(str(self._entry(chat_id).get("model") or ""), self.default_model)

    def set_model(self, chat_id: int, model_key: str) -> str:
        key = resolve_model_key(model_key, self.default_model)
        entry = self._entry(chat_id)
        entry["model"] = key
        self._save()
        return key

    def auto_reply(self, chat_id: int) -> bool:
        return bool(self._entry(chat_id).get("auto_reply", True))

    def set_auto_reply(self, chat_id: int, enabled: bool) -> None:
        entry = self._entry(chat_id)
        entry["auto_reply"] = bool(enabled)
        self._save()

    def nsfw(self, chat_id: int) -> bool:
        """该聊天是否启用成人向外部人设（默认关，需要时手动打开）。"""
        return bool(self._entry(chat_id).get("nsfw", False))

    def set_nsfw(self, chat_id: int, enabled: bool) -> None:
        entry = self._entry(chat_id)
        entry["nsfw"] = bool(enabled)
        self._save()


class ChatMemory:
    """Bounded per-chat recent messages, persisted across bot restarts."""
    def __init__(self, limit: int = 40, *, path: Path | None = None,
                 max_chars: int = 16000, max_chats: int = 128) -> None:
        self.limit = max(1, limit)
        self.path = path
        self.max_chars = max(1, max_chars)
        self.max_chats = max(1, max_chats)
        self._lines: dict[int, deque[dict]] = {}
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return
                for key, rows in list(data.items())[-self.max_chats:]:
                    if not isinstance(rows, list):
                        continue
                    try:
                        chat_id = int(key)
                    except ValueError:
                        continue
                    valid = []
                    for row in rows[-self.limit:]:
                        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
                            continue
                        valid.append({'name': str(row.get('name', '有人'))[:80],
                                      'text': row['text'][:1200],
                                      'message_id': row.get('message_id') if isinstance(row.get('message_id'), int) else None})
                    self._lines[chat_id] = deque(valid, maxlen=self.limit)
            except (OSError, ValueError):
                import logging
                logging.getLogger(__name__).warning('Recent chat memory could not be loaded; starting empty')

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps({str(k): list(v) for k, v in self._lines.items()}, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.path)
        except OSError:
            import logging
            logging.getLogger(__name__).exception('Recent chat memory could not be saved')

    def add(self, chat_id: int, name: str, text: str, *, message_id: int | None = None) -> None:
        cleaned = (text or '').strip()
        if not cleaned:
            return
        rows = self._lines.pop(chat_id, deque(maxlen=self.limit))
        self._lines[chat_id] = rows
        if message_id is not None and any(r['message_id'] == message_id for r in rows):
            return
        rows.append({'name': ' '.join(str(name).split())[:80],
                     'text': cleaned[:1200], 'message_id': message_id})
        while len(self._lines) > self.max_chats:
            self._lines.pop(next(iter(self._lines)))
        self._save()

    def render(self, chat_id: int, *, exclude_message_id: int | None = None) -> str:
        lines = []
        remaining = self.max_chars
        for row in reversed(self._lines.get(chat_id, ())):
            if exclude_message_id is not None and row['message_id'] == exclude_message_id:
                continue
            identifier = f" [消息{row['message_id']}]" if row['message_id'] is not None else ''
            line = f"{row['name']}{identifier}: {row['text']}"
            if len(line) > remaining:
                if not lines:
                    lines.append(line[:remaining])
                break
            lines.append(line)
            remaining -= len(line) + 1
        return '\n'.join(reversed(lines))


class AutoReplyGate:
    def __init__(
        self,
        window_sec: float = 45.0,
        max_hits: int = 1,
        chance: float = 0.4,
    ) -> None:
        self.window_sec = window_sec
        self.max_hits = max_hits
        self.chance = chance
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, chat_id: int) -> bool:
        now = time.monotonic()
        hits = self._hits[chat_id]
        while hits and now - hits[0] > self.window_sec:
            hits.popleft()
        if len(hits) >= self.max_hits:
            return False
        if random.random() >= self.chance:
            return False
        hits.append(now)
        return True

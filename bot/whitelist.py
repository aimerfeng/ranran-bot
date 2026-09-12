from __future__ import annotations

import json
from pathlib import Path


class WhitelistError(ValueError):
    pass


class Whitelist:
    """Group chat_id allow-list. Runtime edits persist to data/whitelist.json."""

    def __init__(self, path: Path, initial_ids: tuple[int, ...] = ()) -> None:
        self.path = path
        self._ids: set[int] = set()
        self._load(initial_ids)

    def _load(self, initial_ids: tuple[int, ...]) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            raw_ids = data.get("group_ids") or []
            self._ids = {int(x) for x in raw_ids if _is_group_id(x)}
            return
        self._ids = {int(x) for x in initial_ids if _is_group_id(x)}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"group_ids": sorted(self._ids)}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def contains(self, chat_id: int) -> bool:
        return chat_id in self._ids

    def add(self, chat_id: int) -> bool:
        chat_id = _require_group_id(chat_id)
        added = chat_id not in self._ids
        self._ids.add(chat_id)
        self._save()
        return added

    def remove(self, chat_id: int) -> bool:
        chat_id = _require_group_id(chat_id)
        existed = chat_id in self._ids
        self._ids.discard(chat_id)
        if existed:
            self._save()
        return existed

    def list_ids(self) -> list[int]:
        return sorted(self._ids)


def _is_group_id(value: object) -> bool:
    try:
        return int(value) < 0
    except (TypeError, ValueError):
        return False


def _require_group_id(chat_id: int) -> int:
    try:
        value = int(chat_id)
    except (TypeError, ValueError) as exc:
        raise WhitelistError("chat_id 必须是整数。") from exc
    if value >= 0:
        raise WhitelistError("白名单只接受群 chat_id（负数 / -100... 超群）。")
    return value


def parse_chat_id(raw: str) -> int:
    text = (raw or "").strip()
    try:
        return int(text)
    except ValueError as exc:
        raise WhitelistError("chat_id 必须是整数，例如 -1001234567890。") from exc

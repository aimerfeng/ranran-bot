"""Observed user messages, isolated by chat, Telegram user ID and Beijing date."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class DailySnapshot:
    chat_id: int
    user_id: int
    day: date
    count: int
    transcript: str
    recording_started_at: str


class DailyJournal:
    def __init__(self, path: Path, retention_days: int = 7) -> None:
        self.path = path
        self.retention_days = max(1, retention_days)
        self._prune_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)',
                       ('recording_started_at', datetime.now(BEIJING).isoformat()))
            db.execute('''CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                day TEXT NOT NULL, sent_at TEXT NOT NULL, updated_at REAL NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS user_day ON messages(chat_id, user_id, day, sent_at, message_id)')
            db.execute('CREATE INDEX IF NOT EXISTS message_day ON messages(day)')
        self.prune(datetime.now(BEIJING).date())

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def add(self, *, chat_id: int, user_id: int, message_id: int, name: str,
            text: str, kind: str, sent_at: datetime, edited_at: datetime | None = None) -> None:
        if not text:
            return
        if sent_at.tzinfo is None or (edited_at is not None and edited_at.tzinfo is None):
            raise ValueError('Telegram timestamps must include timezone')
        original = sent_at.astimezone(BEIJING)
        revision = (edited_at or sent_at).timestamp()
        with closing(self._connect()) as db, db:
            db.execute('''INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                text=excluded.text, kind=excluded.kind, name=excluded.name,
                updated_at=excluded.updated_at
                WHERE excluded.updated_at >= messages.updated_at
                  AND excluded.user_id = messages.user_id''',
                (chat_id, message_id, user_id, original.date().isoformat(), original.isoformat(),
                 revision, name, kind, text))

    def snapshot(self, chat_id: int, user_id: int, day: date) -> DailySnapshot:
        with closing(self._connect()) as db:
            rows = db.execute('''SELECT message_id, sent_at, name, kind, text FROM messages
                WHERE chat_id=? AND user_id=? AND day=? ORDER BY sent_at, message_id''',
                (chat_id, user_id, day.isoformat())).fetchall()
            started = db.execute("SELECT value FROM metadata WHERE key='recording_started_at'").fetchone()[0]
        transcript = '\n'.join(json.dumps({'message_id': mid, 'time': stamp, 'name': name,
                                           'kind': kind, 'text': text}, ensure_ascii=False)
                               for mid, stamp, name, kind, text in rows)
        return DailySnapshot(chat_id, user_id, day, len(rows), transcript, started)

    def search(self, chat_id: int, keyword: str, *, days: int = 3, limit: int = 40) -> list[str]:
        """在最近 N 天的记录里找含关键词的消息，供模型回溯上下文。"""
        word = (keyword or "").strip()
        if not word:
            return []
        escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        since = (datetime.now(BEIJING).date() - timedelta(days=max(1, days) - 1)).isoformat()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT day, sent_at, name, text FROM messages "
                "WHERE chat_id = ? AND day >= ? AND text LIKE ? ESCAPE '\\' "
                "ORDER BY sent_at DESC, message_id DESC LIMIT ?",
                (chat_id, since, f"%{escaped}%", max(1, limit)),
            ).fetchall()
        lines = [f"{row[0]} {row[2]}：{row[3]}" for row in reversed(rows)]
        return lines

    def prune(self, today: date) -> None:
        with self._prune_lock:
            cutoff = (today - timedelta(days=self.retention_days - 1)).isoformat()
            with closing(self._connect()) as db, db:
                db.execute('DELETE FROM messages WHERE day < ?', (cutoff,))

"""轻量持久化：已讲过的书、观众记忆、日志。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import ROOT

DB_PATH = ROOT / "data" / "vera.db"


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS played(
                book_id INTEGER PRIMARY KEY,
                title TEXT,
                score REAL,
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS memory(
                uid TEXT PRIMARY KEY,
                nickname TEXT,
                facts TEXT,
                last_seen REAL
            );
            CREATE TABLE IF NOT EXISTS transcript(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, role TEXT, user TEXT, text TEXT, kind TEXT
            );
            """
        )
        self._conn.commit()

    # ---------- 书籍 ----------
    def played_ids(self) -> set[int]:
        rows = self._conn.execute("SELECT book_id FROM played").fetchall()
        return {int(r[0]) for r in rows}

    def mark_played(self, book_id: int, title: str, score: float) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO played(book_id, title, score, ts) VALUES(?,?,?,?)",
            (book_id, title, score, time.time()),
        )
        self._conn.commit()

    def played_recent(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT book_id, title, score, ts FROM played ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"book_id": r[0], "title": r[1], "score": r[2], "ts": r[3]} for r in rows]

    # ---------- 记忆 ----------
    def remember(self, uid: str, nickname: str, fact: str, limit: int = 12) -> None:
        row = self._conn.execute("SELECT facts FROM memory WHERE uid=?", (uid,)).fetchone()
        facts: list[str] = []
        if row and row[0]:
            try:
                facts = json.loads(row[0])
            except Exception:  # noqa: BLE001
                facts = []
        fact = fact.strip()
        if fact and fact not in facts:
            facts.append(fact)
        facts = facts[-limit:]
        self._conn.execute(
            "INSERT OR REPLACE INTO memory(uid, nickname, facts, last_seen) VALUES(?,?,?,?)",
            (uid, nickname, json.dumps(facts, ensure_ascii=False), time.time()),
        )
        self._conn.commit()

    def recall(self, uid: str) -> list[str]:
        row = self._conn.execute("SELECT facts FROM memory WHERE uid=?", (uid,)).fetchone()
        if not row or not row[0]:
            return []
        try:
            return json.loads(row[0])
        except Exception:  # noqa: BLE001
            return []

    # ---------- 对话记录 ----------
    def log(self, role: str, text: str, user: str = "", kind: str = "") -> None:
        self._conn.execute(
            "INSERT INTO transcript(ts, role, user, text, kind) VALUES(?,?,?,?,?)",
            (time.time(), role, user, text, kind),
        )
        self._conn.commit()

    def recent(self, limit: int = 12) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, role, user, text, kind FROM transcript ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {"ts": r[0], "role": r[1], "user": r[2], "text": r[3], "kind": r[4]}
            for r in reversed(rows)
        ]


store = Store()

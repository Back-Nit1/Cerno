"""SQLite state. Each table keeps a few query columns plus the full model as JSON."""

import json
import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel

TABLES = [
    "sessions", "requirements", "events", "file_changes", "symbol_changes", "evidence",
    "project_checks", "verdicts", "agent_claims", "model_usage", "change_assessments",
    "prompt_timeline", "diff_segments", "attributions",
]
SCHEMA_VERSION = 1


def _schema() -> str:
    rows = [
        f"""CREATE TABLE IF NOT EXISTS {t} (
            pk TEXT PRIMARY KEY, session_id TEXT, item_id TEXT, seq INTEGER, data TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_{t}_session ON {t}(session_id, seq);"""
        for t in TABLES
    ]
    rows.append("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, data TEXT NOT NULL);")
    return "\n".join(rows)


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(_schema())
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def put(self, table: str, session_id: str, item_id: str, item: BaseModel | dict, seq: int = 0) -> None:
        assert table in TABLES
        data = item.model_dump_json() if isinstance(item, BaseModel) else json.dumps(item)
        with self.lock, self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO {table} (pk, session_id, item_id, seq, data) VALUES (?,?,?,?,?)",
                (f"{session_id}:{item_id}", session_id, item_id, seq, data),
            )

    def put_many(self, table: str, session_id: str, items: list[tuple[str, BaseModel | dict]]) -> None:
        for seq, (item_id, item) in enumerate(items):
            self.put(table, session_id, item_id, item, seq)

    def get(self, table: str, session_id: str, item_id: str) -> dict | None:
        assert table in TABLES
        with self.lock:
            row = self.conn.execute(f"SELECT data FROM {table} WHERE pk=?", (f"{session_id}:{item_id}",)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, table: str, session_id: str | None = None) -> list[dict]:
        assert table in TABLES
        with self.lock:
            if session_id is None:
                rows = self.conn.execute(f"SELECT data FROM {table} ORDER BY rowid DESC").fetchall()
            else:
                rows = self.conn.execute(
                    f"SELECT data FROM {table} WHERE session_id=? ORDER BY seq, rowid", (session_id,)
                ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def clear(self, table: str, session_id: str) -> None:
        assert table in TABLES
        with self.lock, self.conn:
            self.conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))

    def count(self, table: str, session_id: str) -> int:
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (session_id,)).fetchone()[0]

    def cache_get(self, key: str):
        with self.lock:
            row = self.conn.execute("SELECT data FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO cache (key, data) VALUES (?,?)", (key, json.dumps(value)))

"""Draft storage: one open draft per guardrail, kept until published or discarded (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Draft:
    bundle_id: str
    base_version: int
    bundle: dict
    created_at: str
    updated_at: str


class DraftStore:
    def __init__(self, path: Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS drafts ("
                " bundle_id TEXT PRIMARY KEY, base_version INTEGER NOT NULL,"
                " bundle TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            self._db.commit()

    @staticmethod
    def _row(row: tuple | None) -> Draft | None:
        if row is None:
            return None
        bundle_id, base_version, bundle, created_at, updated_at = row
        return Draft(bundle_id, base_version, json.loads(bundle), created_at, updated_at)

    def get(self, bundle_id: str) -> Draft | None:
        with self._lock:
            row = self._db.execute(
                "SELECT bundle_id, base_version, bundle, created_at, updated_at FROM drafts"
                " WHERE bundle_id = ?",
                (bundle_id,),
            ).fetchone()
        return self._row(row)

    def create(self, bundle_id: str, base_version: int, bundle: dict) -> Draft:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute(
                "INSERT INTO drafts VALUES (?, ?, ?, ?, ?)",
                (bundle_id, base_version, json.dumps(bundle, ensure_ascii=False), now, now),
            )
            self._db.commit()
        draft = self.get(bundle_id)
        assert draft is not None
        return draft

    def save(self, bundle_id: str, bundle: dict) -> Draft | None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            cursor = self._db.execute(
                "UPDATE drafts SET bundle = ?, updated_at = ? WHERE bundle_id = ?",
                (json.dumps(bundle, ensure_ascii=False), now, bundle_id),
            )
            self._db.commit()
        return self.get(bundle_id) if cursor.rowcount else None

    def delete(self, bundle_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute("DELETE FROM drafts WHERE bundle_id = ?", (bundle_id,))
            self._db.commit()
        return bool(cursor.rowcount)

    def ids(self) -> set[str]:
        with self._lock:
            return {row[0] for row in self._db.execute("SELECT bundle_id FROM drafts")}

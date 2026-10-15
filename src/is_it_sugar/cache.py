"""A small SQLite key-value cache, so repeat checks don't call TypeSafe or FoodData Central again."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS kv (ns TEXT, key TEXT, value TEXT, created REAL, PRIMARY KEY (ns, key))"
        )

    def get(self, ns: str, key: str, max_age: float) -> Any | None:
        row = self._db.execute(
            "SELECT value FROM kv WHERE ns = ? AND key = ? AND created > ?", (ns, key, time.time() - max_age)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def set(self, ns: str, key: str, value: Any) -> None:
        self._db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?, ?)", (ns, key, json.dumps(value), time.time()))

    def drop_other_versions(self, prefixes: tuple[str, ...], version: str) -> None:
        """Delete entries in versioned namespaces ("text:<version>") other than the current version."""
        for prefix in prefixes:
            self._db.execute("DELETE FROM kv WHERE ns LIKE ? AND ns != ?", (f"{prefix}:%", f"{prefix}:{version}"))
        # Unversioned, from before versions.
        self._db.execute(f"DELETE FROM kv WHERE ns IN ({', '.join('?' * len(prefixes))})", prefixes)

    def close(self) -> None:
        self._db.close()

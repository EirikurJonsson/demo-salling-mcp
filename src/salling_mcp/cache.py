"""Small SQLite store behind the Salling client.

It keeps three things that must survive a server restart (the API quota and any
block do not reset when we do):
  * cached API responses,
  * a log of upstream calls, used for the daily call budget,
  * the "blocked until" time after a long Retry-After.

With no path it lives in memory, so tests and `SALLING_CACHE_PATH=off` touch no disk.
Only response bodies and call timestamps are stored, never the API key.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

MAX_CACHE_ROWS = 2000
CALL_WINDOW = 24 * 3600


class Store:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            target = ":memory:"
        else:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            target = str(p)
        self._db = sqlite3.connect(target, check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, expires REAL, value TEXT);
            CREATE TABLE IF NOT EXISTS calls (bucket TEXT, ts REAL);
            CREATE INDEX IF NOT EXISTS calls_bucket_ts ON calls (bucket, ts);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v REAL);
            """
        )
        self._db.execute("DELETE FROM cache WHERE expires <= ?", (time.time(),))
        self._db.execute("DELETE FROM calls WHERE ts <= ?", (time.time() - CALL_WINDOW,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # Responses
    def get(self, key: str) -> tuple[Any, float] | None:
        """Cached value and its remaining lifetime in seconds, or None."""
        row = self._db.execute("SELECT expires, value FROM cache WHERE key = ?", (key,)).fetchone()
        if not row or row[0] <= time.time():
            return None
        return json.loads(row[1]), row[0] - time.time()

    def put(self, key: str, value: Any, ttl: float) -> None:
        self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                         (key, time.time() + ttl, json.dumps(value)))
        self._db.execute(
            "DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY expires DESC LIMIT ?)",
            (MAX_CACHE_ROWS,))
        self._db.commit()

    # Call budget
    def record_call(self, bucket: str) -> None:
        self._db.execute("INSERT INTO calls VALUES (?, ?)", (bucket, time.time()))
        self._db.commit()

    def calls_in_window(self, bucket: str) -> int:
        row = self._db.execute("SELECT COUNT(*) FROM calls WHERE bucket = ? AND ts > ?",
                               (bucket, time.time() - CALL_WINDOW)).fetchone()
        return row[0]

    # Block state (epoch seconds)
    def get_meta(self, name: str) -> float | None:
        row = self._db.execute("SELECT v FROM meta WHERE k = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_meta(self, name: str, value: float) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (name, value))
        self._db.commit()

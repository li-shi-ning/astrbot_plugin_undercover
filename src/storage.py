from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class UndercoverStore:
    """SQLite-backed word pair store for the Undercover plugin."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Create the word pair table when needed."""

        async with self._lock:
            await asyncio.to_thread(self._init_db_sync)

    async def import_builtin_pairs(self, pairs: list[tuple[str, str]]) -> int:
        """Insert built-in CSV pairs without duplicating existing rows.

        Returns:
            Number of newly inserted rows.
        """

        async with self._lock:
            return await asyncio.to_thread(self._import_builtin_pairs_sync, pairs)

    async def add_custom(self, word_a: str, word_b: str) -> int:
        """Add a custom pair and return its row id.

        Raises:
            ValueError: If the input is empty or the pair already exists.
        """

        first, second = self._canonical_pair(word_a, word_b)
        async with self._lock:
            return await asyncio.to_thread(self._add_custom_sync, first, second)

    async def delete_custom(self, pair_id: int) -> bool:
        """Delete one custom pair; built-in rows cannot be deleted."""

        async with self._lock:
            return await asyncio.to_thread(self._delete_custom_sync, int(pair_id))

    async def list_custom(self, limit: int = 50) -> list[dict[str, object]]:
        """List custom pairs, newest first."""

        async with self._lock:
            return await asyncio.to_thread(self._list_custom_sync, int(limit))

    async def stats(self) -> dict[str, int]:
        """Return counts for built-in/custom and used/unused pairs."""

        async with self._lock:
            return await asyncio.to_thread(self._stats_sync)

    async def claim_unused_pair(self) -> tuple[int, tuple[str, str]] | None:
        """Atomically mark one unused pair as used and return it."""

        async with self._lock:
            return await asyncio.to_thread(self._claim_unused_pair_sync)

    async def release_pair(self, pair_id: int) -> None:
        """Mark a claimed pair as unused again after a failed start."""

        async with self._lock:
            await asyncio.to_thread(self._release_pair_sync, int(pair_id))

    @staticmethod
    def _canonical_pair(word_a: str, word_b: str) -> tuple[str, str]:
        first = str(word_a).strip()
        second = str(word_b).strip()
        if not first or not second:
            raise ValueError("词语不能为空。")
        return tuple(sorted((first, second)))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db_sync(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS word_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL DEFAULT '',
                    word_a TEXT NOT NULL,
                    word_b TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'custom',
                    used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (word_a, word_b)
                )
                """
            )
            connection.commit()

    def _import_builtin_pairs_sync(self, pairs: list[tuple[str, str]]) -> int:
        inserted = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            for word_a, word_b in pairs:
                first, second = self._canonical_pair(word_a, word_b)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO word_pairs
                        (category, word_a, word_b, source, used, created_at, updated_at)
                    VALUES (?, ?, ?, 'builtin', 0, ?, ?)
                    """,
                    ("", first, second, now, now),
                )
                inserted += cursor.rowcount
            connection.commit()
        return inserted

    def _add_custom_sync(self, first: str, second: str) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            exists = connection.execute(
                "SELECT id FROM word_pairs WHERE word_a = ? AND word_b = ?",
                (first, second),
            ).fetchone()
            if exists:
                raise ValueError("该词语对已存在。")
            cursor = connection.execute(
                """
                INSERT INTO word_pairs
                    (category, word_a, word_b, source, used, created_at, updated_at)
                VALUES ('custom', ?, ?, 'custom', 0, ?, ?)
                """,
                (first, second, now, now),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def _delete_custom_sync(self, pair_id: int) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM word_pairs WHERE id = ? AND source = 'custom'",
                (pair_id,),
            )
            connection.commit()
            return cursor.rowcount > 0

    def _list_custom_sync(self, limit: int) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, word_a, word_b, used
                FROM word_pairs
                WHERE source = 'custom'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _stats_sync(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN source = 'builtin' THEN 1 ELSE 0 END) AS builtin,
                    SUM(CASE WHEN source = 'custom' THEN 1 ELSE 0 END) AS custom,
                    SUM(CASE WHEN used = 1 THEN 1 ELSE 0 END) AS used,
                    SUM(CASE WHEN used = 0 THEN 1 ELSE 0 END) AS unused
                FROM word_pairs
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "builtin": int(row["builtin"] or 0),
            "custom": int(row["custom"] or 0),
            "used": int(row["used"] or 0),
            "unused": int(row["unused"] or 0),
        }

    def _claim_unused_pair_sync(self) -> tuple[int, tuple[str, str]] | None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id, word_a, word_b
                FROM word_pairs
                WHERE used = 0
                ORDER BY RANDOM()
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE word_pairs SET used = 1, updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            connection.commit()
            return int(row["id"]), (str(row["word_a"]), str(row["word_b"]))

    def _release_pair_sync(self, pair_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE word_pairs SET used = 0, updated_at = ? WHERE id = ?",
                (now, pair_id),
            )
            connection.commit()

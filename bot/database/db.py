"""Async SQLite access layer for stored messages and leaderboard queries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import aiosqlite

from bot.utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _author_exclusion_sql(excluded_user_ids: frozenset[str]) -> tuple[str, list[str]]:
    """SQL fragment and bind values to omit excluded Discord user IDs."""
    if not excluded_user_ids:
        return "", []
    placeholders = ",".join("?" * len(excluded_user_ids))
    return f" AND author_id NOT IN ({placeholders})", sorted(excluded_user_ids)


@dataclass(frozen=True)
class MessageRow:
    """A single scanned message ready to be persisted."""

    message_id: str
    author_id: str
    channel_id: str
    guild_id: str
    created_at: str  # UTC string, see bot.utils.dates.to_db_timestamp
    reaction_count: int
    last_scanned_at: str


class Database:
    """Thin wrapper around an aiosqlite connection."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; call connect() first.")
        return self._conn

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def init_db(self) -> None:
        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self.connection.executescript(schema)
        await self.connection.commit()

    async def delete_messages_for_period(
        self, guild_id: str, after: str, before: str
    ) -> int:
        """Remove existing rows for the period so a re-scan recomputes from scratch."""
        cursor = await self.connection.execute(
            "DELETE FROM messages "
            "WHERE guild_id = ? AND created_at >= ? AND created_at < ?",
            (guild_id, after, before),
        )
        await self.connection.commit()
        deleted = cursor.rowcount
        await cursor.close()
        logger.info("Purged %s rows for guild %s in period.", deleted, guild_id)
        return deleted

    async def upsert_messages(self, rows: Sequence[MessageRow]) -> None:
        if not rows:
            return
        await self.connection.executemany(
            """
            INSERT INTO messages (
                message_id, author_id, channel_id, guild_id,
                created_at, reaction_count, last_scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                reaction_count = excluded.reaction_count,
                last_scanned_at = excluded.last_scanned_at
            """,
            [
                (
                    r.message_id,
                    r.author_id,
                    r.channel_id,
                    r.guild_id,
                    r.created_at,
                    r.reaction_count,
                    r.last_scanned_at,
                )
                for r in rows
            ],
        )
        await self.connection.commit()

    async def upsert_messages_staging(
        self, rows: Sequence[MessageRow], run_id: str
    ) -> None:
        """Stage scanned rows under ``run_id`` without touching prod ``messages``.

        Idempotent on ``(message_id, run_id)``, so a resumed channel re-scan
        simply overwrites its own staged rows.
        """
        if not rows:
            return
        await self.connection.executemany(
            """
            INSERT INTO messages_staging (
                message_id, run_id, author_id, channel_id, guild_id,
                created_at, reaction_count, last_scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id, run_id) DO UPDATE SET
                author_id = excluded.author_id,
                channel_id = excluded.channel_id,
                guild_id = excluded.guild_id,
                created_at = excluded.created_at,
                reaction_count = excluded.reaction_count,
                last_scanned_at = excluded.last_scanned_at
            """,
            [
                (
                    r.message_id,
                    run_id,
                    r.author_id,
                    r.channel_id,
                    r.guild_id,
                    r.created_at,
                    r.reaction_count,
                    r.last_scanned_at,
                )
                for r in rows
            ],
        )
        await self.connection.commit()

    async def discard_staging_run(self, run_id: str) -> int:
        """Drop all staged rows for a run (abort, or a fresh non-resumed run)."""
        cursor = await self.connection.execute(
            "DELETE FROM messages_staging WHERE run_id = ?",
            (run_id,),
        )
        await self.connection.commit()
        deleted = cursor.rowcount
        await cursor.close()
        logger.info("Discarded %s staged rows for run %s.", deleted, run_id)
        return deleted

    async def count_staging_run(self, run_id: str) -> int:
        """Number of staged rows for a run (used to validate a resumed commit)."""
        cursor = await self.connection.execute(
            "SELECT COUNT(*) FROM messages_staging WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0] or 0)

    async def commit_scan_run(
        self, guild_id: str, after: str, before: str, run_id: str
    ) -> int:
        """Atomically replace the period in ``messages`` with the staged run.

        One transaction: purge the period, copy the staged rows (filtered to the
        same guild and period as a guard against stray staging data), then drop
        the staged rows. Rolls back on any error so prod is never left empty.
        Returns the number of rows committed into ``messages``.
        """
        try:
            await self.connection.execute("BEGIN IMMEDIATE")
            await self.connection.execute(
                "DELETE FROM messages "
                "WHERE guild_id = ? AND created_at >= ? AND created_at < ?",
                (guild_id, after, before),
            )
            cursor = await self.connection.execute(
                """
                INSERT INTO messages (
                    message_id, author_id, channel_id, guild_id,
                    created_at, reaction_count, last_scanned_at
                )
                SELECT
                    message_id, author_id, channel_id, guild_id,
                    created_at, reaction_count, last_scanned_at
                FROM messages_staging
                WHERE run_id = ? AND guild_id = ?
                  AND created_at >= ? AND created_at < ?
                """,
                (run_id, guild_id, after, before),
            )
            committed = cursor.rowcount
            await cursor.close()
            await self.connection.execute(
                "DELETE FROM messages_staging WHERE run_id = ?",
                (run_id,),
            )
            await self.connection.commit()
        except Exception:
            await self.connection.rollback()
            logger.exception("commit_scan_run failed for run %s; rolled back.", run_id)
            raise

        logger.info(
            "Committed %s rows into messages for guild %s (run %s).",
            committed,
            guild_id,
            run_id,
        )
        return committed

    async def get_leaderboard(
        self,
        guild_id: str,
        after: str,
        before: str,
        limit: int | None = None,
        *,
        excluded_user_ids: frozenset[str] = frozenset(),
    ) -> list[tuple[str, int]]:
        """Return ``(author_id, total_reactions)`` ordered by reactions desc."""
        exclude_sql, exclude_params = _author_exclusion_sql(excluded_user_ids)
        query = (
            "SELECT author_id, SUM(reaction_count) AS total "
            "FROM messages "
            "WHERE guild_id = ? AND created_at >= ? AND created_at < ?"
            f"{exclude_sql} "
            "GROUP BY author_id "
            "ORDER BY total DESC, author_id ASC"
        )
        params: list[object] = [guild_id, after, before, *exclude_params]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await self.connection.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [(str(author_id), int(total)) for author_id, total in rows]

    async def get_leaderboard_for_channel(
        self,
        guild_id: str,
        channel_id: str,
        after: str,
        before: str,
        limit: int | None = None,
        *,
        excluded_user_ids: frozenset[str] = frozenset(),
    ) -> list[tuple[str, int]]:
        """Return ``(author_id, total_reactions)`` for one channel in the period."""
        exclude_sql, exclude_params = _author_exclusion_sql(excluded_user_ids)
        query = (
            "SELECT author_id, SUM(reaction_count) AS total "
            "FROM messages "
            "WHERE guild_id = ? AND channel_id = ? "
            "AND created_at >= ? AND created_at < ?"
            f"{exclude_sql} "
            "GROUP BY author_id "
            "ORDER BY total DESC, author_id ASC"
        )
        params: list[object] = [guild_id, channel_id, after, before, *exclude_params]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await self.connection.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [(str(author_id), int(total)) for author_id, total in rows]

    async def get_period_audit(
        self, guild_id: str, after: str, before: str
    ) -> dict[str, object]:
        """Return sanity-check counters for rows in the scan period."""
        cursor = await self.connection.execute(
            """
            SELECT
                COUNT(*) AS message_count,
                COUNT(DISTINCT author_id) AS author_count,
                COUNT(DISTINCT channel_id) AS channel_count,
                MIN(created_at) AS min_created_at,
                MAX(created_at) AS max_created_at,
                SUM(reaction_count) AS total_reactions,
                MIN(reaction_count) AS min_reaction_count,
                MAX(reaction_count) AS max_reaction_count
            FROM messages
            WHERE guild_id = ? AND created_at >= ? AND created_at < ?
            """,
            (guild_id, after, before),
        )
        row = await cursor.fetchone()
        await cursor.close()

        cursor = await self.connection.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE guild_id = ? AND created_at >= ? AND created_at < ?
              AND reaction_count <= 0
            """,
            (guild_id, after, before),
        )
        zero_reactions = (await cursor.fetchone())[0]
        await cursor.close()

        cursor = await self.connection.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE guild_id = ?
              AND (created_at < ? OR created_at >= ?)
            """,
            (guild_id, after, before),
        )
        outside_period = (await cursor.fetchone())[0]
        await cursor.close()

        return {
            "message_count": int(row[0] or 0),
            "author_count": int(row[1] or 0),
            "channel_count": int(row[2] or 0),
            "min_created_at": row[3],
            "max_created_at": row[4],
            "total_reactions": int(row[5] or 0),
            "min_reaction_count": row[6],
            "max_reaction_count": row[7],
            "zero_reaction_rows": int(zero_reactions),
            "rows_outside_period": int(outside_period),
        }

    async def get_messages_by_author(
        self,
        guild_id: str,
        after: str,
        before: str,
        author_id: str,
        *,
        limit: int | None = None,
    ) -> list[tuple[str, str, int, str]]:
        """Return messages for an author in the period, newest reactions first.

        Each row is ``(message_id, channel_id, reaction_count, created_at)``.
        """
        query = """
            SELECT message_id, channel_id, reaction_count, created_at
            FROM messages
            WHERE guild_id = ? AND author_id = ?
              AND created_at >= ? AND created_at < ?
            ORDER BY reaction_count DESC, created_at DESC
        """
        params: list[object] = [guild_id, author_id, after, before]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await self.connection.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [(str(m), str(c), int(r), str(ts)) for m, c, r, ts in rows]

    async def get_author_message_breakdown(
        self,
        guild_id: str,
        after: str,
        before: str,
        author_id: str,
        limit: int = 5,
    ) -> list[tuple[str, str, int]]:
        """Return top messages by reaction count for one author (spot-check)."""
        rows = await self.get_messages_by_author(
            guild_id, after, before, author_id, limit=limit
        )
        return [(m, c, r) for m, c, r, _ in rows]


def message_rows_from(items: Iterable[MessageRow]) -> list[MessageRow]:
    """Convenience helper to materialize an iterable of rows."""
    return list(items)

"""Persistence facade for PostgreSQL production and SQLite local storage."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import config

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE players (
  user_id      INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);

CREATE TABLE games (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL REFERENCES players(user_id),
  hidden_slot  INTEGER NOT NULL CHECK (hidden_slot >= 0),
  created_at   INTEGER NOT NULL,
  played_at    INTEGER,
  selected_box INTEGER CHECK (selected_box BETWEEN 0 AND 8),
  won          INTEGER CHECK (won IN (0, 1)),
  CHECK (
    (played_at IS NULL AND selected_box IS NULL AND won IS NULL) OR
    (played_at IS NOT NULL AND selected_box IS NOT NULL AND won IS NOT NULL)
  )
);

CREATE UNIQUE INDEX one_pending_game_per_user
ON games(user_id) WHERE played_at IS NULL;

CREATE INDEX games_user_played_at ON games(user_id, played_at);

CREATE TABLE winners (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id      INTEGER NOT NULL UNIQUE REFERENCES games(id),
  user_id      INTEGER NOT NULL REFERENCES players(user_id),
  display_name TEXT NOT NULL,
  prize_amount INTEGER NOT NULL,
  prize_token  TEXT NOT NULL,
  won_at       INTEGER NOT NULL
);

CREATE TABLE game_messages (
  game_id    INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  chat_id    INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX game_messages_game_id ON game_messages(game_id);

PRAGMA user_version = 2;
"""

MIGRATE_1_TO_2 = """
CREATE TABLE game_messages (
  game_id    INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  chat_id    INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX game_messages_game_id ON game_messages(game_id);
PRAGMA user_version = 2;
"""

_conn: sqlite3.Connection | None = None
_postgres_store: Any | None = None
_lock = threading.RLock()


class StartStatus(Enum):
    READY = "ready"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class StartResult:
    status: StartStatus
    game_id: int | None = None
    retry_after_seconds: int | None = None


class ResolveStatus(Enum):
    WON = "won"
    LOST = "lost"
    NOT_FOUND = "not_found"
    NOT_OWNER = "not_owner"
    ALREADY_RESOLVED = "already_resolved"
    COOLDOWN = "cooldown"


def init(db_path: str | None = None) -> None:
    """Open configured PostgreSQL storage, or an explicit/local SQLite DB."""
    global _conn, _postgres_store
    close()
    if db_path is None and config.DATABASE_URL:
        from postgres_store import PostgresStore

        _postgres_store = PostgresStore(config.DATABASE_URL)
        return

    path = db_path or config.DB_PATH
    connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    version = connection.execute("PRAGMA user_version").fetchone()[0]
    has_tables = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'games'"
    ).fetchone()
    if version == 0 and not has_tables:
        connection.executescript(SCHEMA)
    elif version == 1:
        connection.executescript(MIGRATE_1_TO_2)
    elif version != SCHEMA_VERSION:
        connection.close()
        raise RuntimeError(
            f"Unsupported database schema version {version}; expected {SCHEMA_VERSION}. "
            "Use a new DB_PATH for this clean v1 rebuild."
        )
    _conn = connection


def close() -> None:
    global _conn, _postgres_store
    if _postgres_store is not None:
        _postgres_store.close()
        _postgres_store = None
    if _conn is not None:
        _conn.close()
        _conn = None


def _db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db.init() must be called first")
    return _conn


def _begin() -> sqlite3.Connection:
    connection = _db()
    connection.execute("BEGIN IMMEDIATE")
    return connection


def _upsert_player(
    connection: sqlite3.Connection, user_id: int, display_name: str, now: int
) -> None:
    connection.execute(
        """
        INSERT INTO players (user_id, display_name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          display_name = excluded.display_name,
          updated_at = excluded.updated_at
        """,
        (user_id, display_name, now, now),
    )


def start_or_resume_game(
    user_id: int,
    display_name: str,
    hidden_slot: int,
    cooldown_seconds: int,
    *,
    now: int | None = None,
) -> StartResult:
    """Atomically enforce cooldown and return one pending game per user."""
    if _postgres_store is not None:
        return _postgres_store.start_or_resume_game(
            user_id,
            display_name,
            hidden_slot,
            cooldown_seconds,
            now=now,
        )

    timestamp = int(time.time()) if now is None else now
    with _lock:
        connection = _begin()
        try:
            _upsert_player(connection, user_id, display_name, timestamp)
            last = connection.execute(
                "SELECT MAX(played_at) FROM games WHERE user_id = ? AND played_at IS NOT NULL",
                (user_id,),
            ).fetchone()[0]
            if last is not None and timestamp - last < cooldown_seconds:
                retry_after = cooldown_seconds - (timestamp - last)
                connection.commit()
                return StartResult(
                    StartStatus.COOLDOWN, retry_after_seconds=retry_after
                )

            pending = connection.execute(
                "SELECT id FROM games WHERE user_id = ? AND played_at IS NULL",
                (user_id,),
            ).fetchone()
            if pending is not None:
                connection.commit()
                return StartResult(StartStatus.READY, pending["id"])

            cursor = connection.execute(
                "INSERT INTO games (user_id, hidden_slot, created_at) VALUES (?, ?, ?)",
                (user_id, hidden_slot, timestamp),
            )
            game_id = cursor.lastrowid
            connection.commit()
            return StartResult(StartStatus.READY, game_id)
        except Exception:
            connection.rollback()
            raise


def resolve_game(
    game_id: int,
    user_id: int,
    display_name: str,
    selected_box: int,
    cooldown_seconds: int,
    *,
    now: int | None = None,
) -> ResolveStatus:
    """Resolve a pick and record any winner in one transaction."""
    if _postgres_store is not None:
        return _postgres_store.resolve_game(
            game_id,
            user_id,
            display_name,
            selected_box,
            cooldown_seconds,
            now=now,
        )

    if not 0 <= selected_box < 9:
        return ResolveStatus.NOT_FOUND

    timestamp = int(time.time()) if now is None else now
    with _lock:
        connection = _begin()
        try:
            row = connection.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return ResolveStatus.NOT_FOUND
            if row["user_id"] != user_id:
                connection.commit()
                return ResolveStatus.NOT_OWNER
            if row["played_at"] is not None:
                connection.commit()
                return ResolveStatus.ALREADY_RESOLVED

            last = connection.execute(
                "SELECT MAX(played_at) FROM games WHERE user_id = ? AND played_at IS NOT NULL",
                (user_id,),
            ).fetchone()[0]
            if last is not None and timestamp - last < cooldown_seconds:
                connection.commit()
                return ResolveStatus.COOLDOWN

            won = row["hidden_slot"] == selected_box
            connection.execute(
                """
                UPDATE games
                SET played_at = ?, selected_box = ?, won = ?
                WHERE id = ? AND played_at IS NULL
                """,
                (timestamp, selected_box, int(won), game_id),
            )
            if won:
                connection.execute(
                    """
                    INSERT INTO winners
                      (game_id, user_id, display_name, prize_amount, prize_token, won_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        user_id,
                        display_name,
                        config.PRIZE_AMOUNT,
                        config.PRIZE_TOKEN,
                        timestamp,
                    ),
                )
            connection.commit()
            return ResolveStatus.WON if won else ResolveStatus.LOST
        except Exception:
            connection.rollback()
            raise


def get_game(game_id: int) -> sqlite3.Row | dict[str, Any] | None:
    if _postgres_store is not None:
        return _postgres_store.get_game(game_id)
    with _lock:
        return _db().execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def record_game_message(game_id: int, chat_id: int, message_id: int) -> bool:
    """Register a board message only while its game is still pending."""
    if _postgres_store is not None:
        return _postgres_store.record_game_message(game_id, chat_id, message_id)
    with _lock:
        connection = _db()
        pending = connection.execute(
            "SELECT 1 FROM games WHERE id = ? AND played_at IS NULL", (game_id,)
        ).fetchone()
        if pending is None:
            return False
        connection.execute(
            """
            INSERT INTO game_messages (game_id, chat_id, message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET game_id = excluded.game_id
            """,
            (game_id, chat_id, message_id),
        )
        return True


def list_game_messages(game_id: int) -> list[sqlite3.Row] | list[dict[str, Any]]:
    if _postgres_store is not None:
        return _postgres_store.list_game_messages(game_id)
    with _lock:
        return _db().execute(
            """
            SELECT game_id, chat_id, message_id
            FROM game_messages
            WHERE game_id = ?
            ORDER BY chat_id, message_id
            """,
            (game_id,),
        ).fetchall()


def remove_game_message(chat_id: int, message_id: int) -> None:
    if _postgres_store is not None:
        _postgres_store.remove_game_message(chat_id, message_id)
        return
    with _lock:
        _db().execute(
            "DELETE FROM game_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )


def list_winners() -> list[sqlite3.Row] | list[dict[str, Any]]:
    if _postgres_store is not None:
        return _postgres_store.list_winners()
    with _lock:
        return _db().execute(
            "SELECT * FROM winners ORDER BY won_at DESC, id DESC"
        ).fetchall()


def get_stats() -> tuple[int, int, int]:
    """Return completed games, winners, and unique command users."""
    if _postgres_store is not None:
        return _postgres_store.get_stats()
    with _lock:
        row = _db().execute(
            """
            SELECT
              (SELECT COUNT(*) FROM games WHERE played_at IS NOT NULL) AS games,
              (SELECT COUNT(*) FROM winners) AS winners,
              (SELECT COUNT(*) FROM players) AS players
            """
        ).fetchone()
    return row["games"], row["winners"], row["players"]

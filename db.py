"""SQLite persistence and atomic game-state transitions."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum

import config

SCHEMA_VERSION = 1
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

PRAGMA user_version = 1;
"""

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


class StartStatus(Enum):
    READY = "ready"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class StartResult:
    status: StartStatus
    game_id: int | None = None


class ResolveStatus(Enum):
    WON = "won"
    LOST = "lost"
    NOT_FOUND = "not_found"
    NOT_OWNER = "not_owner"
    ALREADY_RESOLVED = "already_resolved"
    COOLDOWN = "cooldown"


def init(db_path: str | None = None) -> None:
    """Open a database and create the v1 schema."""
    global _conn
    close()
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
    elif version != SCHEMA_VERSION:
        connection.close()
        raise RuntimeError(
            f"Unsupported database schema version {version}; expected {SCHEMA_VERSION}. "
            "Use a new DB_PATH for this clean v1 rebuild."
        )
    _conn = connection


def close() -> None:
    global _conn
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
                connection.commit()
                return StartResult(StartStatus.COOLDOWN)

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


def get_game(game_id: int) -> sqlite3.Row | None:
    with _lock:
        return _db().execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def list_winners() -> list[sqlite3.Row]:
    with _lock:
        return _db().execute(
            "SELECT * FROM winners ORDER BY won_at DESC, id DESC"
        ).fetchall()


def get_stats() -> tuple[int, int, int]:
    """Return completed games, winners, and unique command users."""
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

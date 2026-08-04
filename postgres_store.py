"""PostgreSQL implementation of the Find SolDuck storage contract."""

from __future__ import annotations

import threading
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

import config

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS players (
      user_id      BIGINT PRIMARY KEY,
      display_name TEXT NOT NULL,
      created_at   BIGINT NOT NULL,
      updated_at   BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS games (
      id           BIGSERIAL PRIMARY KEY,
      user_id      BIGINT NOT NULL REFERENCES players(user_id),
      hidden_slot  INTEGER NOT NULL CHECK (hidden_slot >= 0),
      created_at   BIGINT NOT NULL,
      played_at    BIGINT,
      selected_box SMALLINT CHECK (selected_box BETWEEN 0 AND 8),
      won          BOOLEAN,
      CHECK (
        (played_at IS NULL AND selected_box IS NULL AND won IS NULL) OR
        (played_at IS NOT NULL AND selected_box IS NOT NULL AND won IS NOT NULL)
      )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS one_pending_game_per_user
    ON games(user_id) WHERE played_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS games_user_played_at
    ON games(user_id, played_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS winners (
      id           BIGSERIAL PRIMARY KEY,
      game_id      BIGINT NOT NULL UNIQUE REFERENCES games(id),
      user_id      BIGINT NOT NULL REFERENCES players(user_id),
      display_name TEXT NOT NULL,
      prize_amount BIGINT NOT NULL,
      prize_token  TEXT NOT NULL,
      won_at       BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS game_messages (
      game_id    BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
      chat_id    BIGINT NOT NULL,
      message_id BIGINT NOT NULL,
      PRIMARY KEY (chat_id, message_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS game_messages_game_id
    ON game_messages(game_id)
    """,
)


class PostgresStore:
    """Synchronous PostgreSQL store used by the Telegram handlers."""

    def __init__(self, database_url: str) -> None:
        self._lock = threading.RLock()
        self._connection = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=10,
        )
        with self._lock, self._connection.transaction():
            for statement in SCHEMA_STATEMENTS:
                self._connection.execute(statement)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def start_or_resume_game(
        self,
        user_id: int,
        display_name: str,
        hidden_slot: int,
        cooldown_seconds: int,
        *,
        now: int | None = None,
    ):
        from db import StartResult, StartStatus

        timestamp = int(time.time()) if now is None else now
        with self._lock, self._connection.transaction():
            self._connection.execute(
                """
                INSERT INTO players (user_id, display_name, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(user_id) DO UPDATE SET
                  display_name = excluded.display_name,
                  updated_at = excluded.updated_at
                """,
                (user_id, display_name, timestamp, timestamp),
            )
            last_row = self._connection.execute(
                """
                SELECT MAX(played_at) AS last_play
                FROM games
                WHERE user_id = %s AND played_at IS NOT NULL
                """,
                (user_id,),
            ).fetchone()
            last = last_row["last_play"]
            if last is not None and timestamp - last < cooldown_seconds:
                retry_after = cooldown_seconds - (timestamp - last)
                return StartResult(
                    StartStatus.COOLDOWN, retry_after_seconds=retry_after
                )

            pending = self._connection.execute(
                """
                SELECT id FROM games
                WHERE user_id = %s AND played_at IS NULL
                """,
                (user_id,),
            ).fetchone()
            if pending is not None:
                return StartResult(StartStatus.READY, pending["id"])

            game_row = self._connection.execute(
                """
                INSERT INTO games (user_id, hidden_slot, created_at)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (user_id, hidden_slot, timestamp),
            ).fetchone()
            return StartResult(StartStatus.READY, game_row["id"])

    def resolve_game(
        self,
        game_id: int,
        user_id: int,
        display_name: str,
        selected_box: int,
        cooldown_seconds: int,
        *,
        now: int | None = None,
    ):
        from db import ResolveStatus

        if not 0 <= selected_box < 9:
            return ResolveStatus.NOT_FOUND
        timestamp = int(time.time()) if now is None else now
        with self._lock, self._connection.transaction():
            row = self._connection.execute(
                "SELECT * FROM games WHERE id = %s FOR UPDATE", (game_id,)
            ).fetchone()
            if row is None:
                return ResolveStatus.NOT_FOUND
            if row["user_id"] != user_id:
                return ResolveStatus.NOT_OWNER
            if row["played_at"] is not None:
                return ResolveStatus.ALREADY_RESOLVED

            last_row = self._connection.execute(
                """
                SELECT MAX(played_at) AS last_play
                FROM games
                WHERE user_id = %s AND played_at IS NOT NULL
                """,
                (user_id,),
            ).fetchone()
            last = last_row["last_play"]
            if last is not None and timestamp - last < cooldown_seconds:
                return ResolveStatus.COOLDOWN

            won = row["hidden_slot"] == selected_box
            self._connection.execute(
                """
                UPDATE games
                SET played_at = %s, selected_box = %s, won = %s
                WHERE id = %s AND played_at IS NULL
                """,
                (timestamp, selected_box, won, game_id),
            )
            if won:
                self._connection.execute(
                    """
                    INSERT INTO winners
                      (game_id, user_id, display_name, prize_amount, prize_token, won_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
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
            return ResolveStatus.WON if won else ResolveStatus.LOST

    def get_game(self, game_id: int) -> dict[str, Any] | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM games WHERE id = %s", (game_id,)
            ).fetchone()

    def record_game_message(self, game_id: int, chat_id: int, message_id: int) -> bool:
        with self._lock, self._connection.transaction():
            pending = self._connection.execute(
                """
                SELECT 1 FROM games
                WHERE id = %s AND played_at IS NULL
                FOR UPDATE
                """,
                (game_id,),
            ).fetchone()
            if pending is None:
                return False
            self._connection.execute(
                """
                INSERT INTO game_messages (game_id, chat_id, message_id)
                VALUES (%s, %s, %s)
                ON CONFLICT(chat_id, message_id)
                DO UPDATE SET game_id = excluded.game_id
                """,
                (game_id, chat_id, message_id),
            )
            return True

    def list_game_messages(self, game_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT game_id, chat_id, message_id
                FROM game_messages
                WHERE game_id = %s
                ORDER BY chat_id, message_id
                """,
                (game_id,),
            ).fetchall()

    def remove_game_message(self, chat_id: int, message_id: int) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM game_messages WHERE chat_id = %s AND message_id = %s",
                (chat_id, message_id),
            )

    def list_winners(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM winners ORDER BY won_at DESC, id DESC"
            ).fetchall()

    def get_stats(self) -> tuple[int, int, int]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM games WHERE played_at IS NOT NULL) AS games,
                  (SELECT COUNT(*) FROM winners) AS winners,
                  (SELECT COUNT(*) FROM players) AS players
                """
            ).fetchone()
        return row["games"], row["winners"], row["players"]

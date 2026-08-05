import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

import artwork
import bot
import config
import db
import game
import messages
from telegram import BotCommandScopeChat, BotCommandScopeDefault, InputMediaAnimation


@pytest.fixture(autouse=True)
def fresh_database():
    db.init(":memory:")
    yield
    db.close()


def start(user_id=1, name="@alice", hidden_slot=0, now=100):
    return db.start_or_resume_game(
        user_id, name, hidden_slot, cooldown_seconds=86_400, now=now
    )


def resolve(game_id, user_id=1, name="@alice", box=0, now=200):
    return db.resolve_game(
        game_id, user_id, name, box, cooldown_seconds=86_400, now=now
    )


def test_configuration_accepts_v1_defaults(monkeypatch):
    monkeypatch.setattr(config, "WIN_CHANCE", 100)
    monkeypatch.setattr(config, "COOLDOWN_HOURS", 24)
    monkeypatch.setattr(config, "PRIZE_AMOUNT", 10_000)
    monkeypatch.setattr(config, "PRIZE_TOKEN", "SOLDUCK")
    monkeypatch.setattr(config, "DB_PATH", "solduck.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.delenv("RENDER", raising=False)
    config.validate(require_credentials=False)


def test_configuration_rejects_chance_below_box_count(monkeypatch):
    monkeypatch.setattr(config, "WIN_CHANCE", 8)
    with pytest.raises(ValueError, match="at least 9"):
        config.validate(require_credentials=False)


@pytest.mark.parametrize(
    "database_url",
    [
        "mysql://user:password@example.com/database",
        "postgresql:///database",
        "postgresql://example.com",
        "postgresql://[invalid/database",
    ],
)
def test_configuration_rejects_invalid_postgres_url(monkeypatch, database_url):
    monkeypatch.setattr(config, "DATABASE_URL", database_url)
    with pytest.raises(ValueError, match="valid PostgreSQL connection URL"):
        config.validate(require_credentials=False)


def test_render_requires_postgres_for_persistent_state(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(config, "BOT_TOKEN", "token")
    monkeypatch.setattr(config, "ADMIN_IDS", {1})
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://example.com/telegram")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "a-secure_token-123")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with pytest.raises(ValueError, match="DATABASE_URL is required on Render"):
        config.validate()


def test_database_facade_uses_postgres_when_configured(monkeypatch):
    import postgres_store

    store = Mock()
    store.get_stats.return_value = (4, 2, 3)
    constructor = Mock(return_value=store)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://host/database")
    monkeypatch.setattr(postgres_store, "PostgresStore", constructor)

    db.init()

    constructor.assert_called_once_with("postgresql://host/database")
    assert db.get_stats() == (4, 2, 3)
    store.get_stats.assert_called_once_with()
    db.close()
    store.close.assert_called_once_with()


def test_explicit_sqlite_path_overrides_configured_postgres(monkeypatch):
    import postgres_store

    constructor = Mock()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://host/database")
    monkeypatch.setattr(postgres_store, "PostgresStore", constructor)

    db.init(":memory:")

    constructor.assert_not_called()
    assert db.get_stats() == (0, 0, 0)


def test_webhook_configuration_requires_secure_public_url(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_URL", "http://example.com/telegram")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "a-secure_token-123")
    with pytest.raises(ValueError, match="public HTTPS URL"):
        config.validate(require_credentials=False)


def test_webhook_configuration_rejects_weak_secret(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://example.com/telegram")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "short")
    with pytest.raises(ValueError, match="16-256"):
        config.validate(require_credentials=False)


def test_webhook_configuration_rejects_unsupported_public_port(monkeypatch):
    monkeypatch.setattr(
        config, "WEBHOOK_URL", "https://example.com:8080/telegram"
    )
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "a-secure_token-123")
    with pytest.raises(ValueError, match="Telegram-supported port"):
        config.validate(require_credentials=False)


def test_webhook_path_is_derived_from_public_url(monkeypatch):
    monkeypatch.setattr(
        config, "WEBHOOK_URL", "https://example.com/hooks/telegram"
    )
    assert config.webhook_path() == "hooks/telegram"


def test_main_runs_authenticated_webhook(monkeypatch):
    application = SimpleNamespace(run_webhook=Mock())
    monkeypatch.setattr(config, "load", Mock())
    monkeypatch.setattr(config, "WEBHOOK_LISTEN", "0.0.0.0")
    monkeypatch.setattr(config, "PORT", 8080)
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://example.com/telegram")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "a-secure_token-123")
    monkeypatch.setattr(config, "WIN_CHANCE", 100)
    monkeypatch.setattr(bot, "build_application", lambda: application)
    monkeypatch.setattr(db, "init", Mock())
    monkeypatch.setattr(db, "close", Mock())

    bot.main()

    application.run_webhook.assert_called_once_with(
        listen="0.0.0.0",
        port=8080,
        url_path="telegram",
        webhook_url="https://example.com/telegram",
        secret_token="a-secure_token-123",
        allowed_updates=["message", "callback_query"],
        bootstrap_retries=5,
        drop_pending_updates=False,
    )
    db.close.assert_called_once()


def test_command_menus_are_registered_by_scope(monkeypatch):
    telegram_bot = SimpleNamespace(set_my_commands=AsyncMock())
    application = SimpleNamespace(bot=telegram_bot)
    monkeypatch.setattr(config, "ADMIN_IDS", {22, 11})

    asyncio.run(bot.register_commands(application))

    calls = telegram_bot.set_my_commands.await_args_list
    assert len(calls) == 1
    public_commands, public_scope = calls[0].args[0], calls[0].kwargs["scope"]
    assert [command.command for command in public_commands] == ["findsolduck"]
    assert isinstance(public_scope, BotCommandScopeDefault)


def test_admin_menu_is_registered_after_private_interaction(monkeypatch):
    telegram_bot = SimpleNamespace(set_my_commands=AsyncMock())
    context = SimpleNamespace(bot=telegram_bot)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=11),
        effective_chat=SimpleNamespace(id=11, type="private"),
    )
    monkeypatch.setattr(config, "ADMIN_IDS", {11})

    asyncio.run(bot.register_private_admin_commands(update, context))

    commands = telegram_bot.set_my_commands.await_args.args[0]
    scope = telegram_bot.set_my_commands.await_args.kwargs["scope"]
    assert [command.command for command in commands] == [
        "findsolduck",
        "winnerlist",
        "stats",
    ]
    assert isinstance(scope, BotCommandScopeChat)
    assert scope.chat_id == 11


def test_http_client_logs_do_not_expose_telegram_urls():
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_docker_database_defaults_to_app_owned_directory():
    dockerfile = (
        Path(__file__).resolve().parents[1] / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "DB_PATH=/app/data/solduck.db" in dockerfile
    assert "mkdir -p /app/data" in dockerfile
    assert "chown solduck:solduck /app/data" in dockerfile
    assert "COPY --chown=solduck:solduck assets ./assets" in dockerfile


def test_game_and_winner_artwork_are_bundled():
    for image_path in (bot.GAME_BOARD_IMAGE, artwork.WINNER_TEMPLATE):
        assert image_path.is_file()
    assert bot.GAME_BOARD_IMAGE.read_bytes().startswith(b"\xff\xd8\xff")
    assert artwork.WINNER_TEMPLATE.read_bytes().startswith(b"\x89PNG")
    assert artwork.WINNER_FONT.is_file()
    assert bot.LOSER_ANIMATION.is_file()
    assert bot.LOSER_ANIMATION.suffix == ".mp4"
    assert bot.LOSER_ANIMATION.stat().st_size < 10 * 1024 * 1024


def test_personalized_winner_artwork_is_valid_telegram_jpeg():
    rendered = artwork.render_winner_image("@alice")
    with Image.open(rendered) as image:
        assert image.format == "JPEG"
        assert image.size == (1254, 1254)
    assert rendered.getbuffer().nbytes < 10 * 1024 * 1024


def test_personalized_artwork_changes_with_winner_and_handles_long_names():
    alice = artwork.render_winner_image("@alice").getvalue()
    bob = artwork.render_winner_image("@bob").getvalue()
    long_name = artwork.render_winner_image("Duck " * 100).getvalue()

    assert alice != bob
    assert long_name.startswith(b"\xff\xd8\xff")


def test_winner_label_removes_line_breaks_and_has_fallback():
    assert artwork.winner_label("Alice\nDuck") == "WINNER: Alice Duck"
    assert artwork.winner_label("Alice\u202eDuck") == "WINNER: AliceDuck"
    assert len(artwork.winner_label("x" * 1_000)) <= len("WINNER: ") + 64
    assert artwork.winner_label("  ") == "WINNER: SolDuck Player"


def test_hidden_slot_uses_configured_probability_space(monkeypatch):
    monkeypatch.setattr(config, "WIN_CHANCE", 100)
    seen = {}

    def fake_randbelow(limit):
        seen["limit"] = limit
        return 42

    monkeypatch.setattr(game.secrets, "randbelow", fake_randbelow)
    assert game.generate_hidden_slot() == 42
    assert seen["limit"] == 100


@pytest.mark.parametrize("box", range(9))
def test_each_box_wins_only_for_its_matching_slot(box):
    assert game.is_winning_pick(box, box)
    assert not game.is_winning_pick((box + 1) % 9, box)
    assert not game.is_winning_pick(99, box)


def test_keyboard_is_a_three_by_three_grid():
    keyboard = bot.build_keyboard(17)
    assert len(keyboard.inline_keyboard) == 3
    assert all(len(row) == 3 for row in keyboard.inline_keyboard)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == [f"📦 {box}" for box in range(1, 10)]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == [f"pick:17:{box}" for box in range(9)]


def test_admin_can_complete_games_without_cooldown(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", {1})
    admin_cooldown = bot.cooldown_seconds_for(1)
    assert admin_cooldown == 0
    assert bot.cooldown_seconds_for(2) == config.COOLDOWN_HOURS * 3600

    first = db.start_or_resume_game(1, "@admin", 0, admin_cooldown, now=100)
    assert (
        db.resolve_game(first.game_id, 1, "@admin", 0, admin_cooldown, now=100)
        is db.ResolveStatus.WON
    )
    second = db.start_or_resume_game(1, "@admin", 1, admin_cooldown, now=100)
    assert second.status is db.StartStatus.READY
    assert second.game_id != first.game_id


def test_opening_board_does_not_start_cooldown_and_reuses_pending_game():
    first = start(hidden_slot=7, now=100)
    second = start(hidden_slot=2, now=500)
    assert first.status is db.StartStatus.READY
    assert second == first
    assert db.get_stats() == (0, 0, 1)
    assert db.get_game(first.game_id)["hidden_slot"] == 7


def test_cooldown_starts_on_tap_and_expires_at_exact_boundary():
    first = start(now=100)
    assert resolve(first.game_id, now=200) is db.ResolveStatus.WON
    blocked = start(now=200 + 1)
    assert blocked.status is db.StartStatus.COOLDOWN
    assert blocked.retry_after_seconds == 86_399
    after = start(hidden_slot=8, now=200 + 86_400)
    assert after.status is db.StartStatus.READY
    assert after.game_id != first.game_id


def test_wrong_user_cannot_resolve_game():
    pending = start()
    assert resolve(pending.game_id, user_id=2, name="@mallory") is db.ResolveStatus.NOT_OWNER
    assert db.get_game(pending.game_id)["played_at"] is None


def test_invalid_box_is_rejected_without_resolving():
    pending = start()
    assert resolve(pending.game_id, box=9) is db.ResolveStatus.NOT_FOUND
    assert db.get_game(pending.game_id)["played_at"] is None


def test_resolved_game_accepts_only_one_tap():
    pending = start(hidden_slot=3)
    assert resolve(pending.game_id, box=3) is db.ResolveStatus.WON
    assert resolve(pending.game_id, box=4, now=201) is db.ResolveStatus.ALREADY_RESOLVED
    assert len(db.list_winners()) == 1


def test_concurrent_taps_produce_one_result_and_one_winner():
    pending = start(hidden_slot=5)

    def tap():
        return resolve(pending.game_id, box=5, now=300)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: tap(), range(2)))
    assert sorted(result.value for result in results) == ["already_resolved", "won"]
    assert len(db.list_winners()) == 1


def test_losing_game_and_stats_count_only_completed_games():
    pending = start(hidden_slot=50)
    start(user_id=2, name="Bob", hidden_slot=1)
    assert resolve(pending.game_id, box=4) is db.ResolveStatus.LOST
    assert db.get_stats() == (1, 0, 2)


def test_winner_stores_prize_and_display_name_snapshot(monkeypatch):
    monkeypatch.setattr(config, "PRIZE_AMOUNT", 10_000)
    monkeypatch.setattr(config, "PRIZE_TOKEN", "SOLDUCK")
    pending = start(name="@alice", hidden_slot=2)
    assert resolve(pending.game_id, name="Alice Updated", box=2) is db.ResolveStatus.WON
    winner = db.list_winners()[0]
    assert winner["display_name"] == "Alice Updated"
    assert winner["prize_amount"] == 10_000
    assert winner["prize_token"] == "SOLDUCK"


def test_display_name_prefers_username_and_has_safe_fallbacks():
    assert bot.display_name(SimpleNamespace(username="alice", full_name="Alice", id=1)) == "@alice"
    assert bot.display_name(SimpleNamespace(username=None, full_name="Alice Duck", id=2)) == "Alice Duck"
    assert bot.display_name(SimpleNamespace(username=None, full_name="", id=3)) == "User 3"


def test_losing_messages_are_selected_from_configured_pool():
    for _ in range(30):
        assert game.random_losing_message() in messages.LOSING_MESSAGES


def test_cooldown_message_uses_configured_duration(monkeypatch):
    monkeypatch.setattr(config, "COOLDOWN_HOURS", 48)
    text = messages.cooldown_message()
    assert "48 hours" in text
    assert "tomorrow" not in text.lower()


def test_cooldown_message_shows_exact_time_remaining():
    text = messages.cooldown_message(90_061)
    assert "1d 1h 1m 1s" in text


def test_winner_date_is_formatted_in_utc():
    line = messages.format_winner_line("@john123", 10_000, "SOLDUCK", 1785456000)
    assert line == "@john123\n10,000 SOLDUCK\nJuly 31, 2026"


def test_winnerlist_is_split_below_telegram_limit():
    rows = [
        {
            "display_name": f"@winner_{index}",
            "prize_amount": 10_000,
            "prize_token": "SOLDUCK",
            "won_at": 1785456000,
        }
        for index in range(200)
    ]
    chunks = messages.winnerlist_chunks(rows, limit=250)
    assert len(chunks) > 1
    assert all(len(chunk) <= 250 for chunk in chunks)
    assert sum(chunk.count("10,000 SOLDUCK") for chunk in chunks) == 200


def test_single_oversized_winner_entry_is_split_below_limit():
    rows = [
        {
            "display_name": "@winner",
            "prize_amount": 10_000,
            "prize_token": "X" * 5000,
            "won_at": 1785456000,
        }
    ]
    chunks = messages.winnerlist_chunks(rows)
    expected = "🏆 Winners\n\n" + messages.format_winner_line(
        "@winner", 10_000, "X" * 5000, 1785456000
    )
    assert all(len(chunk) <= messages.TELEGRAM_TEXT_LIMIT for chunk in chunks)
    assert "".join(chunks) == expected


def test_malformed_numeric_environment_has_concise_startup_error():
    environment = os.environ.copy()
    environment["WIN_CHANCE"] = "invalid"
    result = subprocess.run(
        [sys.executable, "bot.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.strip() == "Configuration error: WIN_CHANCE must be an integer"


class FakeQuery:
    def __init__(self, data, user, chat_id=100, message_id=200):
        self.data = data
        self.from_user = user
        self.message = SimpleNamespace(chat_id=chat_id, message_id=message_id)
        self.answers = []
        self.edited_text = None
        self.edited_caption = None
        self.edited_media = None
        self.reply_markup_edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text):
        self.edited_text = text

    async def edit_message_caption(self, caption):
        self.edited_caption = caption

    async def edit_message_media(self, media):
        self.edited_media = media

    async def edit_message_reply_markup(self, reply_markup=None):
        self.reply_markup_edits.append(reply_markup)


class FakeMessage:
    def __init__(self, chat_id=100, reply_message_id=200):
        self.chat_id = chat_id
        self.reply_message_id = reply_message_id
        self.replies = []
        self.photos = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))
        return SimpleNamespace(
            chat_id=self.chat_id,
            message_id=self.reply_message_id,
            edit_text=self._edit_text,
        )

    async def _edit_text(self, text):
        self.replies.append((text, None))

    async def reply_photo(self, photo, caption=None, reply_markup=None):
        self.photos.append((photo, caption, reply_markup))
        return SimpleNamespace(
            chat_id=self.chat_id,
            message_id=self.reply_message_id,
            edit_reply_markup=AsyncMock(),
            edit_caption=AsyncMock(),
        )


class FakeBot:
    def __init__(self):
        self.edits = []
        self.caption_edits = []
        self.reply_markup_edits = []

    async def edit_message_text(self, text, chat_id, message_id):
        self.edits.append((text, chat_id, message_id))

    async def edit_message_caption(self, caption, chat_id, message_id):
        self.caption_edits.append((caption, chat_id, message_id))

    async def edit_message_reply_markup(
        self, chat_id, message_id, reply_markup=None
    ):
        self.reply_markup_edits.append((reply_markup, chat_id, message_id))


def test_find_command_registers_each_duplicate_board():
    pending = start(user_id=1, hidden_slot=4)
    user = SimpleNamespace(id=1, username="alice", full_name="Alice")
    first_message = FakeMessage(chat_id=10, reply_message_id=101)
    second_message = FakeMessage(chat_id=20, reply_message_id=202)

    asyncio.run(
        bot.find_solduck(
            SimpleNamespace(effective_user=user, effective_message=first_message), None
        )
    )
    asyncio.run(
        bot.find_solduck(
            SimpleNamespace(effective_user=user, effective_message=second_message), None
        )
    )

    locations = {
        (row["chat_id"], row["message_id"])
        for row in db.list_game_messages(pending.game_id)
    }
    assert locations == {(10, 101), (20, 202)}
    assert first_message.photos[0][0].filename == "game-board.jpg"
    assert second_message.photos[0][0].filename == "game-board.jpg"
    assert first_message.photos[0][1] == messages.game_prompt()
    assert second_message.photos[0][1] == messages.game_prompt()


def test_resolving_one_board_closes_every_duplicate():
    pending = start(user_id=1, hidden_slot=4)
    db.record_game_message(pending.game_id, chat_id=10, message_id=101)
    db.record_game_message(pending.game_id, chat_id=20, message_id=202)
    user = SimpleNamespace(id=1, username="alice", full_name="Alice")
    query = FakeQuery(
        f"pick:{pending.game_id}:4", user, chat_id=10, message_id=101
    )
    context = SimpleNamespace(bot=FakeBot())

    asyncio.run(bot.handle_pick(SimpleNamespace(callback_query=query), context))

    assert query.edited_media.caption == messages.winner_message("@alice")
    assert query.edited_media.media.filename == "winner.jpg"
    assert context.bot.caption_edits == [(messages.GAME_OVER_MESSAGE, 20, 202)]
    assert context.bot.reply_markup_edits == [(None, 20, 202)]


def test_tapping_stale_duplicate_replaces_its_board():
    pending = start(user_id=1, hidden_slot=4)
    db.record_game_message(pending.game_id, chat_id=20, message_id=202)
    assert resolve(pending.game_id, box=4) is db.ResolveStatus.WON
    user = SimpleNamespace(id=1, username="alice", full_name="Alice")
    query = FakeQuery(
        f"pick:{pending.game_id}:4", user, chat_id=20, message_id=202
    )

    asyncio.run(
        bot.handle_pick(
            SimpleNamespace(callback_query=query), SimpleNamespace(bot=FakeBot())
        )
    )

    assert query.edited_caption == messages.GAME_OVER_MESSAGE
    assert query.reply_markup_edits == [None]
    assert db.list_game_messages(pending.game_id) == []


def test_schema_v1_database_is_migrated_for_board_tracking(tmp_path):
    database_path = tmp_path / "schema-v1.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE players (user_id INTEGER PRIMARY KEY);
        CREATE TABLE games (id INTEGER PRIMARY KEY);
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    db.init(str(database_path))

    probe = sqlite3.connect(database_path)
    version = probe.execute("PRAGMA user_version").fetchone()[0]
    table = probe.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'game_messages'"
    ).fetchone()
    probe.close()
    assert version == 2
    assert table[0] == "game_messages"


def test_callback_handler_rejects_another_users_game():
    pending = start(user_id=1, hidden_slot=0)
    user = SimpleNamespace(id=2, username="mallory", full_name="Mallory")
    query = FakeQuery(f"pick:{pending.game_id}:0", user)
    update = SimpleNamespace(callback_query=query)
    asyncio.run(bot.handle_pick(update, None))
    assert query.answers == [(messages.NOT_YOUR_GAME_MESSAGE, True)]
    assert query.edited_text is None


def test_callback_handler_rejects_malformed_data():
    user = SimpleNamespace(id=1, username="alice", full_name="Alice")
    query = FakeQuery("pick:1:9", user)
    update = SimpleNamespace(callback_query=query)
    asyncio.run(bot.handle_pick(update, None))
    assert query.answers == [(messages.GAME_NOT_FOUND_MESSAGE, True)]


def test_callback_handler_records_and_displays_winner():
    pending = start(user_id=1, hidden_slot=4)
    user = SimpleNamespace(id=1, username="alice", full_name="Alice")
    query = FakeQuery(f"pick:{pending.game_id}:4", user)
    update = SimpleNamespace(callback_query=query)
    asyncio.run(bot.handle_pick(update, None))
    assert query.answers == [(None, False)]
    assert query.edited_media.caption == messages.winner_message("@alice")
    assert query.edited_media.media.filename == "winner.jpg"
    assert query.reply_markup_edits == [None]
    assert len(db.list_winners()) == 1


def test_winner_caption_identifies_player_with_full_name_fallback():
    pending = start(user_id=7, hidden_slot=4)
    user = SimpleNamespace(id=7, username=None, full_name="Alice Duck")
    query = FakeQuery(f"pick:{pending.game_id}:4", user)

    asyncio.run(
        bot.handle_pick(
            SimpleNamespace(callback_query=query), SimpleNamespace(bot=FakeBot())
        )
    )

    assert query.edited_media.caption.startswith("🏆 Winner: Alice Duck\n")


def test_callback_handler_displays_losing_result_as_animation():
    pending = start(user_id=1, hidden_slot=50)
    user = SimpleNamespace(id=1, username="alice", full_name="Alice")
    query = FakeQuery(f"pick:{pending.game_id}:4", user)

    asyncio.run(
        bot.handle_pick(
            SimpleNamespace(callback_query=query), SimpleNamespace(bot=FakeBot())
        )
    )

    assert query.edited_media.caption in messages.LOSING_MESSAGES
    assert isinstance(query.edited_media, InputMediaAnimation)
    assert query.edited_media.media.filename == "loser-animation.mp4"
    assert b"ftyp" in query.edited_media.media.input_file_content[:32]
    assert query.edited_caption is None
    assert query.reply_markup_edits == [None]


def test_admin_command_rejects_unconfigured_user(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", {99})
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1), effective_message=message
    )
    asyncio.run(bot.stats(update, None))
    assert message.replies == [(messages.ADMINS_ONLY_MESSAGE, None)]

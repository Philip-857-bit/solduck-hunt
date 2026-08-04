import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import bot
import config
import db
import game
import messages


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
    config.validate(require_credentials=False)


def test_configuration_rejects_chance_below_box_count(monkeypatch):
    monkeypatch.setattr(config, "WIN_CHANCE", 8)
    with pytest.raises(ValueError, match="at least 9"):
        config.validate(require_credentials=False)


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
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == [f"pick:17:{box}" for box in range(9)]


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
    assert start(now=200 + 86_399).status is db.StartStatus.COOLDOWN
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
    def __init__(self, data, user):
        self.data = data
        self.from_user = user
        self.answers = []
        self.edited_text = None

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text):
        self.edited_text = text


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


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
    assert query.edited_text == messages.winner_message()
    assert len(db.list_winners()) == 1


def test_admin_command_rejects_unconfigured_user(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", {99})
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1), effective_message=message
    )
    asyncio.run(bot.stats(update, None))
    assert message.replies == [(messages.ADMINS_ONLY_MESSAGE, None)]

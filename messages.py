"""User-facing text and Telegram-safe formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import config

TELEGRAM_TEXT_LIMIT = 4096


def prize_text() -> str:
    return f"{config.PRIZE_AMOUNT:,} {config.PRIZE_TOKEN}"


def game_prompt() -> str:
    return (
        "🦆 Find SolDuck!\n\n"
        "SolDuck is hiding behind one of these 9 boxes.\n\n"
        "Pick ONE box.\n\n"
        f"🎁 Prize: {prize_text()} Tokens"
    )


def winner_message() -> str:
    return (
        "🎉 Congratulations!\n\n"
        "🦆 You found SolDuck!\n\n"
        "🏆 You won:\n"
        f"{prize_text()} Tokens\n\n"
        "Your prize has been recorded.\n"
        "An admin will send your reward."
    )


def cooldown_message() -> str:
    unit = "hour" if config.COOLDOWN_HOURS == 1 else "hours"
    return (
        "⏳ You already played.\n\n"
        f"You can play once every {config.COOLDOWN_HOURS} {unit}.\n"
        f"Come back when your cooldown ends for another chance to win "
        f"{prize_text()} Tokens!"
    )


LOSING_MESSAGES = (
    "😂 SolDuck flew away!",
    "🥚 You found an egg.",
    "🐸 Just a frog.",
    "🦊 Wrong animal!",
    "🚀 SolDuck escaped!",
    "💩 Better luck next time!",
)

NOT_YOUR_GAME_MESSAGE = "This isn't your game! 🦆"
GAME_OVER_MESSAGE = "This game is already over."
GAME_NOT_FOUND_MESSAGE = "Game not found."
ADMINS_ONLY_MESSAGE = "⛔ Admins only."
NO_WINNERS_MESSAGE = "No winners yet."


def format_winner_line(
    display_name: str, prize_amount: int, prize_token: str, won_at: int
) -> str:
    date = datetime.fromtimestamp(won_at, tz=timezone.utc)
    date_text = f"{date.strftime('%B')} {date.day}, {date.year}"
    return f"{display_name}\n{prize_amount:,} {prize_token}\n{date_text}"


def winnerlist_chunks(rows, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Format all winners into messages below Telegram's text limit."""
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    entries = [
        format_winner_line(
            row["display_name"],
            row["prize_amount"],
            row["prize_token"],
            row["won_at"],
        )
        for row in rows
    ]
    remaining = "🏆 Winners\n\n" + "\n\n".join(entries)
    chunks: list[str] = []
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit + 1)
        delimiter_length = 2
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, limit + 1)
            delimiter_length = 1
        if split_at <= 0:
            split_at = limit
        else:
            split_at = min(split_at + delimiter_length, limit)
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return chunks

"""Environment-driven configuration for Find SolDuck."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# Safe defaults keep imports side-effect free. ``load()`` applies environment
# values at startup, inside the entry point's configuration error boundary.
BOT_TOKEN = ""
ADMIN_IDS: set[int] = set()
WIN_CHANCE = 100
COOLDOWN_HOURS = 24
PRIZE_AMOUNT = 10_000
PRIZE_TOKEN = "SOLDUCK"
DB_PATH = "solduck.db"


def _integer(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _admin_ids() -> set[int]:
    try:
        return {
            int(part.strip())
            for part in os.environ.get("ADMIN_IDS", "").split(",")
            if part.strip()
        }
    except ValueError as exc:
        raise ValueError(
            "ADMIN_IDS must contain comma-separated numeric Telegram IDs"
        ) from exc


def load(*, require_credentials: bool = True) -> None:
    """Parse environment values, then validate the complete configuration."""
    global BOT_TOKEN, ADMIN_IDS, WIN_CHANCE, COOLDOWN_HOURS
    global PRIZE_AMOUNT, PRIZE_TOKEN, DB_PATH

    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    admin_ids = _admin_ids()
    win_chance = _integer("WIN_CHANCE", "100")
    cooldown_hours = _integer("COOLDOWN_HOURS", "24")
    prize_amount = _integer("PRIZE_AMOUNT", "10000")
    prize_token = os.environ.get("PRIZE_TOKEN", "SOLDUCK").strip()
    db_path = os.environ.get("DB_PATH", "solduck.db").strip()

    BOT_TOKEN = bot_token
    ADMIN_IDS = admin_ids
    WIN_CHANCE = win_chance
    COOLDOWN_HOURS = cooldown_hours
    PRIZE_AMOUNT = prize_amount
    PRIZE_TOKEN = prize_token
    DB_PATH = db_path
    validate(require_credentials=require_credentials)


def validate(*, require_credentials: bool = True) -> None:
    """Fail fast with actionable configuration errors."""
    if require_credentials and not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is required")
    if require_credentials and not ADMIN_IDS:
        raise ValueError("ADMIN_IDS must contain at least one Telegram user ID")
    if WIN_CHANCE < 9:
        raise ValueError("WIN_CHANCE must be at least 9 for a nine-box game")
    if COOLDOWN_HOURS <= 0:
        raise ValueError("COOLDOWN_HOURS must be greater than zero")
    if PRIZE_AMOUNT <= 0:
        raise ValueError("PRIZE_AMOUNT must be greater than zero")
    if not PRIZE_TOKEN:
        raise ValueError("PRIZE_TOKEN cannot be empty")
    if not DB_PATH:
        raise ValueError("DB_PATH cannot be empty")

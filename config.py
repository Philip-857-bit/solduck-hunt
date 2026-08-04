"""Environment-driven configuration for Find SolDuck."""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

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
DATABASE_URL = ""
WEBHOOK_URL = ""
WEBHOOK_SECRET = ""
WEBHOOK_LISTEN = "0.0.0.0"
PORT = 8080

_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


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
    global DATABASE_URL
    global WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_LISTEN, PORT

    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    admin_ids = _admin_ids()
    win_chance = _integer("WIN_CHANCE", "100")
    cooldown_hours = _integer("COOLDOWN_HOURS", "24")
    prize_amount = _integer("PRIZE_AMOUNT", "10000")
    prize_token = os.environ.get("PRIZE_TOKEN", "SOLDUCK").strip()
    db_path = os.environ.get("DB_PATH", "solduck.db").strip()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    webhook_url = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    webhook_listen = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0").strip()
    port = _integer("PORT", "8080")

    BOT_TOKEN = bot_token
    ADMIN_IDS = admin_ids
    WIN_CHANCE = win_chance
    COOLDOWN_HOURS = cooldown_hours
    PRIZE_AMOUNT = prize_amount
    PRIZE_TOKEN = prize_token
    DB_PATH = db_path
    DATABASE_URL = database_url
    WEBHOOK_URL = webhook_url
    WEBHOOK_SECRET = webhook_secret
    WEBHOOK_LISTEN = webhook_listen
    PORT = port
    validate(require_credentials=require_credentials)


def webhook_path() -> str:
    """Return the local URL path corresponding to WEBHOOK_URL."""
    return urlsplit(WEBHOOK_URL).path.lstrip("/")


def validate(*, require_credentials: bool = True) -> None:
    """Fail fast with actionable configuration errors."""
    if require_credentials and not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is required")
    if require_credentials and not ADMIN_IDS:
        raise ValueError("ADMIN_IDS must contain at least one Telegram user ID")
    if require_credentials and not WEBHOOK_URL:
        raise ValueError("WEBHOOK_URL is required")
    if require_credentials and not WEBHOOK_SECRET:
        raise ValueError("WEBHOOK_SECRET is required")
    if (
        require_credentials
        and os.environ.get("RENDER", "").lower() == "true"
        and not DATABASE_URL
    ):
        raise ValueError("DATABASE_URL is required on Render for persistent storage")
    if WIN_CHANCE < 9:
        raise ValueError("WIN_CHANCE must be at least 9 for a nine-box game")
    if COOLDOWN_HOURS <= 0:
        raise ValueError("COOLDOWN_HOURS must be greater than zero")
    if PRIZE_AMOUNT <= 0:
        raise ValueError("PRIZE_AMOUNT must be greater than zero")
    if not PRIZE_TOKEN:
        raise ValueError("PRIZE_TOKEN cannot be empty")
    if not DATABASE_URL and not DB_PATH:
        raise ValueError("DB_PATH cannot be empty")
    if DATABASE_URL:
        try:
            parsed_database_url = urlsplit(DATABASE_URL)
            database_hostname = parsed_database_url.hostname
        except ValueError as exc:
            raise ValueError(
                "DATABASE_URL must be a valid PostgreSQL connection URL"
            ) from exc
        if (
            parsed_database_url.scheme not in ("postgres", "postgresql")
            or not database_hostname
            or not parsed_database_url.path.strip("/")
        ):
            raise ValueError("DATABASE_URL must be a valid PostgreSQL connection URL")
    if not WEBHOOK_LISTEN:
        raise ValueError("WEBHOOK_LISTEN cannot be empty")
    if not 1 <= PORT <= 65_535:
        raise ValueError("PORT must be between 1 and 65535")
    if WEBHOOK_URL:
        parsed_url = urlsplit(WEBHOOK_URL)
        try:
            public_port = parsed_url.port
        except ValueError as exc:
            raise ValueError("WEBHOOK_URL contains an invalid port") from exc
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
            or not webhook_path()
        ):
            raise ValueError(
                "WEBHOOK_URL must be a public HTTPS URL with a path and no "
                "credentials, query, or fragment"
            )
        if public_port not in (None, 80, 88, 443, 8443):
            raise ValueError(
                "WEBHOOK_URL must use Telegram-supported port 443, 80, 88, or 8443"
            )
    if WEBHOOK_SECRET and not _WEBHOOK_SECRET_RE.fullmatch(WEBHOOK_SECRET):
        raise ValueError(
            "WEBHOOK_SECRET must be 16-256 characters using only letters, "
            "numbers, underscores, or hyphens"
        )

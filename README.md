# 🦆 Find SolDuck Bot (v1.0)

A lightweight Telegram game where each user may search for SolDuck once every
24 hours. A winning pick records a 10,000 SOLDUCK reward for manual fulfillment
by an administrator. There is no wallet connection or blockchain integration.

## Game rules

- `/findsolduck` shows a 3×3 inline-button board.
- The first valid box tap completes the game and starts the cooldown.
- Re-running the command before tapping restores the same pending game.
- Any selected box has exactly a 1-in-`WIN_CHANCE` chance to win (1 in 100 by
  default). Slots 0–8 represent the boxes and all remaining slots represent
  boards where SolDuck flew away.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Obtain the numeric Telegram user IDs that should have admin access.
3. Install and configure the bot:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in `BOT_TOKEN` and `ADMIN_IDS` in `.env`, then run:

```powershell
python bot.py
```

The bot uses Telegram long polling, so v1 does not require a domain or webhook.

## Docker

Build the image and run it with credentials from `.env` and a named volume for
the SQLite database:

```powershell
docker build -t solduck-bot .
docker run -d --name solduck-bot --restart unless-stopped `
  --env-file .env `
  --mount source=solduck-data,target=/data `
  solduck-bot
```

The image runs as an unprivileged user, does not copy `.env` into the image, and
uses `/data/solduck.db` by default. Back up the `solduck-data` volume to preserve
cooldowns and winner records when moving hosts.

## Commands

| Command | Access | Description |
|---|---|---|
| `/findsolduck` | Everyone | Start or restore the daily game |
| `/winnerlist` | Admins | Show every recorded winner, newest first |
| `/stats` | Admins | Show completed games, winners, and players |

## Configuration

| Variable | Default | Notes |
|---|---:|---|
| `BOT_TOKEN` | — | Required token from BotFather |
| `ADMIN_IDS` | — | Required comma-separated numeric user IDs |
| `WIN_CHANCE` | `100` | Must be at least 9 |
| `COOLDOWN_HOURS` | `24` | Rolling cooldown after a box tap |
| `PRIZE_AMOUNT` | `10000` | Recorded with each winner |
| `PRIZE_TOKEN` | `SOLDUCK` | Recorded with each winner |
| `DB_PATH` | `solduck.db` | SQLite database location |

## Tests

```powershell
python -m pytest -q
```

The code is split into Telegram handlers (`bot.py`), atomic SQLite operations
(`db.py`), pure game rules (`game.py`), configuration (`config.py`), and message
formatting (`messages.py`) so additional games can be added later.

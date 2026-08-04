# 🦆 Find SolDuck Bot (v1.0)

A lightweight Telegram game where each user may search for SolDuck once every
24 hours. A winning pick records a 10,000 SOLDUCK reward for manual fulfillment
by an administrator. There is no wallet connection or blockchain integration.

## Game rules

- `/findsolduck` shows a 3×3 inline-button board.
- The first valid box tap completes the game and starts the cooldown.
- Cooldown responses show the exact days, hours, minutes, and seconds remaining.
- Re-running the command before tapping restores the same pending game.
- When one board is resolved, every duplicate board is closed automatically.
- Boxes use numbered `📦` buttons for a clearer 3×3 board.
- Any selected box has exactly a 1-in-`WIN_CHANCE` chance to win (1 in 100 by
  default). Slots 0–8 represent the boxes and all remaining slots represent
  boards where SolDuck flew away.
- Configured admins have no play cooldown and can test consecutive games.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Obtain the numeric Telegram user IDs that should have admin access.
3. Prepare a public HTTPS endpoint such as
   `https://bot.example.com/telegram` that proxies to the bot's internal port.
4. Install and configure the bot:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in the required values in `.env`. Generate a webhook secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then run:

```powershell
python bot.py
```

At startup the bot registers `WEBHOOK_URL` with Telegram and verifies every
request using Telegram's `X-Telegram-Bot-Api-Secret-Token` header. TLS should be
terminated by the hosting platform or a reverse proxy; the built-in listener
serves HTTP internally on `WEBHOOK_LISTEN:PORT`.

## Docker

Build the image and run it with credentials from `.env`:

```powershell
docker build -t solduck-bot .
docker run -d --name solduck-bot --restart unless-stopped `
  --env-file .env `
  --publish 8080:8080 `
  solduck-bot
```

The image runs as an unprivileged user, does not copy `.env` into the image, and
uses PostgreSQL whenever `DATABASE_URL` is set. For local SQLite use, omit
`DATABASE_URL` and optionally mount a named volume at `/app/data` to preserve
`/app/data/solduck.db` across container replacements.

### Render

For a Render Docker web service:

1. Create a PostgreSQL database (on Render or another provider).
2. Set `DATABASE_URL` on the bot service to its PostgreSQL connection URL. Use
   Render's internal database URL when both services are in the same region.
3. Set `WEBHOOK_URL` to the public service URL plus the webhook path, such as
   `https://your-service.onrender.com/telegram`.

The bot intentionally refuses to start on Render without `DATABASE_URL` because
free web-service filesystems are ephemeral. PostgreSQL preserves cooldowns,
active boards, winner records, and statistics across redeploys and spin-downs.

## Commands

| Command | Access | Description |
|---|---|---|
| `/findsolduck` | Everyone | Start or restore the daily game |
| `/winnerlist` | Admins | Show every recorded winner, newest first |
| `/stats` | Admins | Show completed games, winners, and players |

The bot registers `/findsolduck` automatically at startup. Telegram creates an
admin's scoped menu only after that admin messages the bot privately; it then
adds `/winnerlist` and `/stats` to that private-chat menu. Handler authorization
still checks the sender's numeric Telegram ID.

## Configuration

| Variable | Default | Notes |
|---|---:|---|
| `BOT_TOKEN` | — | Required token from BotFather |
| `ADMIN_IDS` | — | Required comma-separated numeric user IDs |
| `WEBHOOK_URL` | — | Required public HTTPS URL including its path |
| `WEBHOOK_SECRET` | — | Required 16–256 character request-verification secret |
| `WEBHOOK_LISTEN` | `0.0.0.0` | Internal listener address |
| `PORT` | `8080` | Internal listener port |
| `WIN_CHANCE` | `100` | Must be at least 9 |
| `COOLDOWN_HOURS` | `24` | Rolling cooldown after a box tap |
| `PRIZE_AMOUNT` | `10000` | Recorded with each winner |
| `PRIZE_TOKEN` | `SOLDUCK` | Recorded with each winner |
| `DATABASE_URL` | — | PostgreSQL URL; required on Render and preferred in production |
| `DB_PATH` | Runtime-specific | SQLite fallback only; local default `solduck.db`, Docker default `/app/data/solduck.db` |

## Tests

```powershell
python -m pytest -q
```

The code is split into Telegram handlers (`bot.py`), a storage facade (`db.py`),
PostgreSQL production storage (`postgres_store.py`), pure game rules (`game.py`),
configuration (`config.py`), and message formatting (`messages.py`) so additional
games can be added later.

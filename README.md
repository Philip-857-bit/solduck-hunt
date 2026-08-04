# 🦆 Find SolDuck Bot (v1.0)

A lightweight Telegram game where each user may search for SolDuck once every
24 hours. A winning pick records a 10,000 SOLDUCK reward for manual fulfillment
by an administrator. There is no wallet connection or blockchain integration.

## Game rules

- `/findsolduck` shows a 3×3 inline-button board.
- The first valid box tap completes the game and starts the cooldown.
- Re-running the command before tapping restores the same pending game.
- When one board is resolved, every duplicate board is closed automatically.
- Any selected box has exactly a 1-in-`WIN_CHANCE` chance to win (1 in 100 by
  default). Slots 0–8 represent the boxes and all remaining slots represent
  boards where SolDuck flew away.

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

Build the image and run it with credentials from `.env` and a named volume for
the SQLite database:

```powershell
docker build -t solduck-bot .
docker run -d --name solduck-bot --restart unless-stopped `
  --env-file .env `
  --publish 8080:8080 `
  --mount source=solduck-data,target=/app/data `
  solduck-bot
```

The image runs as an unprivileged user, does not copy `.env` into the image, and
uses `/app/data/solduck.db` by default. Back up the `solduck-data` volume to
preserve cooldowns and winner records when moving hosts.

### Render

For a Render Docker web service, set `WEBHOOK_URL` to your service URL plus the
webhook path, for example `https://your-service.onrender.com/telegram`. Do not
set `DB_PATH=solduck.db`; either omit it to use `/app/data/solduck.db` or set that
absolute path explicitly.

Render free web services have an ephemeral filesystem and cannot attach a
persistent disk. The bot will run, but SQLite cooldowns, statistics, and winner
records are erased whenever the service restarts, redeploys, or spins down. For
reliable rewards, use a paid Render persistent disk mounted at `/app/data` or
move storage to an external database.

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
| `WEBHOOK_URL` | — | Required public HTTPS URL including its path |
| `WEBHOOK_SECRET` | — | Required 16–256 character request-verification secret |
| `WEBHOOK_LISTEN` | `0.0.0.0` | Internal listener address |
| `PORT` | `8080` | Internal listener port |
| `WIN_CHANCE` | `100` | Must be at least 9 |
| `COOLDOWN_HOURS` | `24` | Rolling cooldown after a box tap |
| `PRIZE_AMOUNT` | `10000` | Recorded with each winner |
| `PRIZE_TOKEN` | `SOLDUCK` | Recorded with each winner |
| `DB_PATH` | Runtime-specific | Local default `solduck.db`; Docker default `/app/data/solduck.db` |

## Tests

```powershell
python -m pytest -q
```

The code is split into Telegram handlers (`bot.py`), atomic SQLite operations
(`db.py`), pure game rules (`game.py`), configuration (`config.py`), and message
formatting (`messages.py`) so additional games can be added later.

"""Find SolDuck Telegram bot entry point."""

from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
import db
import game
import messages

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PICK_RE = re.compile(r"^pick:(\d+):([0-8])$")


def build_keyboard(game_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton("⬜", callback_data=f"pick:{game_id}:{box}")
        for box in range(game.BOX_COUNT)
    ]
    return InlineKeyboardMarkup(
        [buttons[index : index + 3] for index in range(0, game.BOX_COUNT, 3)]
    )


def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    full_name = (user.full_name or "").strip()
    return full_name or f"User {user.id}"


def cooldown_seconds() -> int:
    return config.COOLDOWN_HOURS * 3600


async def find_solduck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    result = db.start_or_resume_game(
        user.id,
        display_name(user),
        game.generate_hidden_slot(),
        cooldown_seconds(),
    )
    if result.status is db.StartStatus.COOLDOWN:
        await message.reply_text(messages.cooldown_message())
        return

    await message.reply_text(
        messages.game_prompt(), reply_markup=build_keyboard(result.game_id)
    )


async def handle_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    match = PICK_RE.fullmatch(query.data or "")
    if match is None:
        await query.answer(messages.GAME_NOT_FOUND_MESSAGE, show_alert=True)
        return

    game_id, selected_box = int(match.group(1)), int(match.group(2))
    result = db.resolve_game(
        game_id,
        query.from_user.id,
        display_name(query.from_user),
        selected_box,
        cooldown_seconds(),
    )

    alerts = {
        db.ResolveStatus.NOT_FOUND: messages.GAME_NOT_FOUND_MESSAGE,
        db.ResolveStatus.NOT_OWNER: messages.NOT_YOUR_GAME_MESSAGE,
        db.ResolveStatus.ALREADY_RESOLVED: messages.GAME_OVER_MESSAGE,
        db.ResolveStatus.COOLDOWN: messages.cooldown_message(),
    }
    if result in alerts:
        await query.answer(alerts[result], show_alert=True)
        return

    await query.answer()
    if result is db.ResolveStatus.WON:
        logger.info("Winner recorded for Telegram user %s, game %s", query.from_user.id, game_id)
        await query.edit_message_text(messages.winner_message())
    else:
        await query.edit_message_text(game.random_losing_message())


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def winnerlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not _is_admin(user.id):
        await message.reply_text(messages.ADMINS_ONLY_MESSAGE)
        return

    rows = db.list_winners()
    if not rows:
        await message.reply_text(messages.NO_WINNERS_MESSAGE)
        return
    for chunk in messages.winnerlist_chunks(rows):
        await message.reply_text(chunk)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not _is_admin(user.id):
        await message.reply_text(messages.ADMINS_ONLY_MESSAGE)
        return

    total_games, total_winners, total_players = db.get_stats()
    await message.reply_text(
        "📊 Stats\n\n"
        f"Total games played: {total_games}\n"
        f"Total winners: {total_winners}\n"
        f"Total players: {total_players}"
    )


async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram update error", exc_info=context.error)


def build_application() -> Application:
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("findsolduck", find_solduck))
    app.add_handler(CommandHandler("winnerlist", winnerlist))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(handle_pick, pattern=r"^pick:"))
    app.add_error_handler(log_error)
    return app


def main() -> None:
    try:
        config.load()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    db.init()
    app = build_application()
    logger.info("Find SolDuck is running with a 1-in-%s win chance", config.WIN_CHANCE)
    try:
        app.run_polling()
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""Find SolDuck Telegram bot entry point."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Update,
)
from telegram.error import TelegramError
from telegram.constants import ChatType
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
import artwork

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PICK_RE = re.compile(r"^pick:(\d+):([0-8])$")
ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets"
GAME_BOARD_IMAGE = ASSET_DIRECTORY / "game-board.jpg"

PUBLIC_COMMANDS = (BotCommand("findsolduck", "Play Find SolDuck"),)
ADMIN_COMMANDS = PUBLIC_COMMANDS + (
    BotCommand("winnerlist", "Show recorded winners"),
    BotCommand("stats", "Show game statistics"),
)


async def register_commands(application: Application) -> None:
    """Register the public command menu with Telegram."""
    try:
        await application.bot.set_my_commands(
            PUBLIC_COMMANDS, scope=BotCommandScopeDefault()
        )
    except TelegramError as exc:
        logger.warning("Could not register the public command menu: %s", exc)


async def register_private_admin_commands(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Register admin commands after Telegram has established the private chat."""
    user = update.effective_user
    chat = getattr(update, "effective_chat", None)
    if (
        context is None
        or user is None
        or chat is None
        or chat.type != ChatType.PRIVATE
        or user.id not in config.ADMIN_IDS
    ):
        return
    try:
        await context.bot.set_my_commands(
            ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=chat.id)
        )
    except TelegramError as exc:
        logger.warning("Could not register the private admin command menu: %s", exc)


def build_keyboard(game_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            f"📦 {box + 1}", callback_data=f"pick:{game_id}:{box}"
        )
        for box in range(game.BOX_COUNT)
    ]
    return InlineKeyboardMarkup(
        [buttons[index : index + 3] for index in range(0, game.BOX_COUNT, 3)]
    )


def image_upload(path: Path) -> InputFile:
    """Build a reusable Telegram upload without leaving a file handle open."""
    return InputFile(path.read_bytes(), filename=path.name)


def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    full_name = (user.full_name or "").strip()
    return full_name or f"User {user.id}"


def cooldown_seconds_for(user_id: int) -> int:
    """Admins may play repeatedly; all other users use the configured cooldown."""
    if user_id in config.ADMIN_IDS:
        return 0
    return config.COOLDOWN_HOURS * 3600


async def find_solduck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    await register_private_admin_commands(update, context)

    result = db.start_or_resume_game(
        user.id,
        display_name(user),
        game.generate_hidden_slot(),
        cooldown_seconds_for(user.id),
    )
    if result.status is db.StartStatus.COOLDOWN:
        await message.reply_text(
            messages.cooldown_message(result.retry_after_seconds)
        )
        return

    board_message = await message.reply_photo(
        photo=image_upload(GAME_BOARD_IMAGE),
        caption=messages.game_prompt(),
        reply_markup=build_keyboard(result.game_id),
    )
    if not db.record_game_message(
        result.game_id, board_message.chat_id, board_message.message_id
    ):
        await board_message.edit_reply_markup(reply_markup=None)
        await board_message.edit_caption(caption=messages.GAME_OVER_MESSAGE)


def _query_location(query) -> tuple[int, int] | None:
    message = query.message
    if message is None:
        return None
    return message.chat_id, message.message_id


async def _close_duplicate_boards(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: int,
    current_location: tuple[int, int] | None,
) -> None:
    if context is None:
        return
    for row in db.list_game_messages(game_id):
        location = row["chat_id"], row["message_id"]
        if location == current_location:
            continue
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                reply_markup=None,
            )
            await context.bot.edit_message_caption(
                caption=messages.GAME_OVER_MESSAGE,
                chat_id=row["chat_id"],
                message_id=row["message_id"],
            )
        except TelegramError as exc:
            logger.warning(
                "Could not close board message %s in chat %s: %s",
                row["message_id"],
                row["chat_id"],
                exc,
            )
        else:
            db.remove_game_message(row["chat_id"], row["message_id"])


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
        cooldown_seconds_for(query.from_user.id),
    )

    alerts = {
        db.ResolveStatus.NOT_FOUND: messages.GAME_NOT_FOUND_MESSAGE,
        db.ResolveStatus.NOT_OWNER: messages.NOT_YOUR_GAME_MESSAGE,
        db.ResolveStatus.COOLDOWN: messages.cooldown_message(),
    }
    if result in alerts:
        await query.answer(alerts[result], show_alert=True)
        return

    current_location = _query_location(query)
    if result is db.ResolveStatus.ALREADY_RESOLVED:
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_caption(caption=messages.GAME_OVER_MESSAGE)
        except TelegramError:
            logger.warning("Could not close already-resolved board", exc_info=True)
        else:
            if current_location is not None:
                db.remove_game_message(*current_location)
        await _close_duplicate_boards(context, game_id, current_location)
        return

    await query.answer()
    result_text: str
    if result is db.ResolveStatus.WON:
        logger.info("Winner recorded for Telegram user %s, game %s", query.from_user.id, game_id)
        result_text = messages.winner_message(display_name(query.from_user))
    else:
        result_text = game.random_losing_message()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        if result is db.ResolveStatus.WON:
            await query.edit_message_media(
                media=InputMediaPhoto(
                    media=InputFile(
                        artwork.render_winner_image(
                            display_name(query.from_user)
                        ),
                        filename="winner.jpg",
                    ),
                    caption=result_text,
                )
            )
        else:
            await query.edit_message_caption(caption=result_text)
    except TelegramError:
        logger.warning("Could not replace selected board with its result", exc_info=True)
    else:
        if current_location is not None:
            db.remove_game_message(*current_location)
    await _close_duplicate_boards(context, game_id, current_location)


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def winnerlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    await register_private_admin_commands(update, context)
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
    await register_private_admin_commands(update, context)
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
    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(register_commands)
        .build()
    )
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
    logger.info(
        "Find SolDuck is serving %s with a 1-in-%s win chance",
        config.WEBHOOK_URL,
        config.WIN_CHANCE,
    )
    try:
        app.run_webhook(
            listen=config.WEBHOOK_LISTEN,
            port=config.PORT,
            url_path=config.webhook_path(),
            webhook_url=config.WEBHOOK_URL,
            secret_token=config.WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
            bootstrap_retries=5,
            drop_pending_updates=False,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()

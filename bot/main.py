import logging
import sys
import time
from telegram import BotCommand
from telegram.ext import ApplicationBuilder

from bot.config import BOT_TOKEN, BOT_API_URL
from bot.handlers import get_handlers

# ── Logging setup ──
logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
# Reduce noise from httpx / telegram internals
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(application) -> None:
    """Set bot commands on startup."""
    commands = [
        BotCommand("start", "Start the bot & welcome message"),
        BotCommand("id", "Get your Telegram User ID"),
        BotCommand("help", "How to use the bot"),
        BotCommand("status", "Check bot load & queue"),
        BotCommand("stats", "Global download statistics"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("✅ Bot commands registered.")


def main() -> None:
    logger.info("🚀 Starting Video Downloader Bot...")

    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(300)    # увеличить для больших файлов
        .write_timeout(300)
        .connect_timeout(30)
    )

    # +++ Подключение к локальному Bot API +++
    if BOT_API_URL:
        # python-telegram-bot ожидает base_url вида: http://host:port/bot
        base_url = BOT_API_URL.rstrip("/") + "/bot"
        base_file_url = BOT_API_URL.rstrip("/") + "/file/bot"
        builder = builder.base_url(base_url).base_file_url(base_file_url)
        logger.info(f"🔗 Using local Bot API: {base_url}")
    else:
        logger.info("🌐 Using official Telegram Bot API")

    app = builder.post_init(post_init).build()

    for handler in get_handlers():
        app.add_handler(handler)

    logger.info("Bot is ready. Polling for messages...")
    time.sleep(3)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

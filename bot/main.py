import logging
import sys
import time

from telegram import BotCommand
from telegram.ext import ApplicationBuilder

from bot.config import BOT_TOKEN, BOT_API_URL, LOCAL_MODE
from bot.downloader import purge_stale_downloads
from bot.handlers import get_handlers, error_handler

logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(application) -> None:
    # Остатки после OOM (exit 137) / падения во время upload
    removed = purge_stale_downloads()
    if removed:
        logger.info("🧹 Removed %d stale files from downloads", removed)

    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot & welcome message"),
        BotCommand("id", "Get your Telegram User ID"),
        BotCommand("help", "How to use the bot"),
        BotCommand("status", "Check bot load & queue"),
        BotCommand("stats", "Global download statistics"),
    ])
    logger.info("✅ Bot commands registered.")


def main() -> None:
    logger.info("🚀 Starting Video Downloader Bot...")

    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        # ВАЖНО: без этого PTB обрабатывает апдейты строго по одному —
        # одна долгая загрузка блокирует всех пользователей и даже /status,
        # а семафоры queue_manager фактически не работают.
        .concurrent_updates(True)
        .read_timeout(300)
        .write_timeout(300)
        .connect_timeout(30)
    )

    if BOT_API_URL:
        base = BOT_API_URL.rstrip("/")
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot").local_mode(LOCAL_MODE)
        logger.info("🔗 Using local Bot API: %s (local_mode=%s)", base, LOCAL_MODE)
    else:
        logger.info("🌐 Using official Telegram Bot API")

    app = builder.post_init(post_init).build()
    for handler in get_handlers():
        app.add_handler(handler)
    app.add_error_handler(error_handler)

    logger.info("Bot is ready. Polling for messages...")
    time.sleep(3)  # дать telegram-bot-api подняться; при неудаче спасёт restart: unless-stopped
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

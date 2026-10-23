import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from telegram import BotCommand
from telegram.ext import ApplicationBuilder

from bot.config import (BOT_TOKEN, BOT_API_URL, LOCAL_MODE, DOWNLOAD_DIR, COOKIES_FILE, VK_COOKIES_FILE,
                        ADMIN_IDS, DAILY_REPORT, DAILY_REPORT_TIME, DAILY_REPORT_TZ, DATA_DIR)
from bot import activity, handlers
from bot.preflight import run_checks
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

HEARTBEAT_FILE = Path("/tmp/bot.heartbeat")
HEARTBEAT_INTERVAL = 30
_background: set[asyncio.Task] = set()


async def _heartbeat() -> None:
    """Пульс для HEALTHCHECK: обновляется, пока жив event loop."""
    warned = False
    while True:
        try:
            HEARTBEAT_FILE.touch()
            warned = False
        except OSError as e:
            if not warned:  # не засорять лог каждые 30 с
                logger.warning("heartbeat failed: %s", e)
                warned = True
        await asyncio.sleep(HEARTBEAT_INTERVAL)


def _start_daily_report(application) -> None:
    if not DAILY_REPORT:
        return
    if not ADMIN_IDS:
        logger.warning("DAILY_REPORT=1, но ADMIN_IDS пуст — отчёт выключен")
        return
    tz = activity.parse_tz(DAILY_REPORT_TZ)
    at = activity.parse_time(DAILY_REPORT_TIME)
    log = activity.ActivityLog(DATA_DIR, tz)
    if not log.enabled:
        return
    handlers.activity_log = log
    handlers.REPORT_TZ_NAME = str(tz)
    log.cleanup()
    task = asyncio.create_task(activity.report_loop(
        application.bot, log, activity.ReportState(DATA_DIR), ADMIN_IDS, at, str(tz)))
    _background.add(task)
    task.add_done_callback(_background.discard)
    logger.info("📊 Daily report: %s %s -> %d admin(s)", at.strftime("%H:%M"), tz, len(ADMIN_IDS))


async def post_init(application) -> None:
    task = asyncio.create_task(_heartbeat())
    _background.add(task)
    task.add_done_callback(_background.discard)

    _start_daily_report(application)

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
        BotCommand("report", "Today's activity report (admins)"),
    ])
    logger.info("✅ Bot commands registered.")


def main() -> None:
    logger.info("🚀 Starting Video Downloader Bot (uid=%s)...", os.getuid())
    if not run_checks(DOWNLOAD_DIR, {"COOKIES_FILE": COOKIES_FILE, "VK_COOKIES_FILE": VK_COOKIES_FILE}):
        # Без записи в downloads работать нельзя. Пауза — чтобы restart: unless-stopped
        # не крутил контейнер в цикле и не засорял лог.
        time.sleep(60)
        sys.exit(1)

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

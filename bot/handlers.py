import asyncio
import contextlib
import logging
import time
import uuid
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat, MessageEntity
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.config import (
    SUPPORTED_PLATFORMS,
    ADMIN_IDS,
    COOLDOWN_SECONDS,
    LOCAL_MODE,
    MAX_FILE_SIZE_BYTES,
    WARNING_THRESHOLD_BYTES,
    PENDING_URL_TTL,
)
from bot.downloader import (
    download_video_async,
    get_video_info,
    estimate_size,
    has_free_space,
    is_live,
    cleanup_file,
    DownloadError,
    FileTooLargeError,
)
from bot.media import prepare_video
from bot.utils import extract_urls, identify_platform, format_file_size, get_file_size, _escape_html
from bot.stats import stats
from bot import queue_manager

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TITLE_LIMIT = 200
# В local mode запрос короткий, но сервер отвечает только после загрузки в Telegram
UPLOAD_TIMEOUT = 1800

_user_last_request: dict[int, float] = {}

# short_id -> (url, created_at). Обходит лимит callback_data в 64 байта.
_pending_urls: dict[str, tuple[str, float]] = {}


def _store_url(url: str) -> str:
    now = time.monotonic()
    for k in [k for k, (_, ts) in _pending_urls.items() if now - ts > PENDING_URL_TTL]:
        _pending_urls.pop(k, None)
    short_id = uuid.uuid4().hex[:8]
    _pending_urls[short_id] = (url, now)
    return short_id


def _get_url(short_id: str, pop: bool = False) -> str | None:
    item = _pending_urls.pop(short_id, None) if pop else _pending_urls.get(short_id)
    if not item:
        return None
    url, ts = item
    if time.monotonic() - ts > PENDING_URL_TTL:
        _pending_urls.pop(short_id, None)
        return None
    return url


def _format_keyboard(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎬 Video", callback_data=f"dl|v|{sid}"),
        InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl|a|{sid}"),
    ]])


def _format_prompt(platform: str) -> str:
    return f"🎯 <b>Found {platform} link!</b>\nChoose your format:"


async def _safe_edit(query, text: str, **kwargs) -> None:
    try:
        await query.edit_message_text(text, **kwargs)
    except TelegramError as e:
        logger.debug("edit_message_text failed: %s", e)


@contextlib.asynccontextmanager
async def _chat_action(chat: Chat, action: str):
    """send_action живёт ~5 секунд — продлеваем, пока идёт работа."""
    async def loop():
        while True:
            with contextlib.suppress(TelegramError):
                await chat.send_action(action)
            await asyncio.sleep(4.5)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _build_caption(result: dict, platform: str, duration: int, file_size: int) -> str:
    title = result.get("title") or "Video"
    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT - 1] + "…"
    icon = "🎵" if result.get("audio_only") else "🎬"
    caption = (
        f"{icon} <b>{_escape_html(title)}</b>\n"
        f"👤 {_escape_html(result.get('uploader') or 'Unknown')}\n"
        f"📱 {platform}"
    )
    if duration:
        mins, secs = divmod(int(duration), 60)
        caption += f"  ⏱ {mins}:{secs:02d}"
    caption += f"\n📦 {format_file_size(file_size)}"
    return caption[:CAPTION_LIMIT]


def _media_input(path: Path, stack: contextlib.ExitStack):
    """В local mode PTB превращает Path в file:// URI — сервер читает файл сам, без HTTP-загрузки."""
    if LOCAL_MODE:
        return path
    return stack.enter_context(open(path, "rb"))


# ───────────────────────────── commands ─────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats.record_user(update.effective_user.id)
    platforms = ", ".join(SUPPORTED_PLATFORMS.keys())
    text = (
        "👋 <b>Welcome to the Video Downloader Bot!</b>\n\n"
        "I can download videos from:\n"
        f"<i>{platforms}</i>\n\n"
        "⚡ <b>How to use:</b>\n"
        "Just send me a link, and I'll ask if you want it as a <b>Video</b> or <b>Audio (MP3)</b>.\n\n"
        "💡 <i>Tip: You can send multiple links in one message!</i>\n"
        "👤 <i>Need your ID? Use /id</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your Telegram ID is: {update.effective_user.id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [
        "📖 <b>Usage Guide</b>\n",
        "1. Copy a URL from a supported site.",
        "2. Paste it here.",
        "3. Choose the format (Video/Audio).",
        "4. Wait for the file to be processed.\n",
        "<b>Supported Platforms:</b>",
    ]
    lines += [f"• {p}" for p in sorted(SUPPORTED_PLATFORMS.keys())]
    lines += ["\n<b>Commands:</b>", "/status - Check bot load & queue", "/stats - Global download statistics"]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🛰 <b>Bot Status</b>\n\n"
        f"Active downloads: <b>{queue_manager.active_downloads()}</b>\n"
        f"Waiting in queue: <b>{queue_manager.queue_depth()}</b>\n\n"
        "✅ The bot is running normally."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🔒 This command is restricted to admins.")
        return
    await update.message.reply_text(stats.summary_text(), parse_mode=ParseMode.HTML)


# ───────────────────────────── links ─────────────────────────────

def _message_urls(message) -> list[str]:
    """URL из текста/подписи + из entities (включая скрытые гиперссылки text_link)."""
    kinds = [MessageEntity.URL, MessageEntity.TEXT_LINK]
    entities = {**message.parse_entities(kinds), **message.parse_caption_entities(kinds)}
    parts = [message.text or message.caption or ""]
    for ent, value in entities.items():
        parts.append(ent.url if ent.type == MessageEntity.TEXT_LINK and ent.url else value)
    return extract_urls("\n".join(p for p in parts if p))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not (message.text or message.caption):
        return

    user_id = update.effective_user.id
    stats.record_user(user_id)

    urls = _message_urls(message)
    if not urls:
        return

    supported = [u for u in urls if identify_platform(u)]
    logger.info("Links from %s: %s (supported: %d)", user_id, urls, len(supported))
    if not supported:
        # В группах молчим, чтобы не спамить; в личке объясняем
        if message.chat.type == ChatType.PRIVATE:
            await message.reply_text(
                "🤷 Эта ссылка не поддерживается. Список платформ — /help",
                reply_to_message_id=message.message_id,
            )
        return

    now = time.monotonic()
    last = _user_last_request.get(user_id, 0.0)
    if now - last < COOLDOWN_SECONDS:
        await message.reply_text(f"⏳ Slow down! Wait {int(COOLDOWN_SECONDS - (now - last)) + 1}s.")
        return
    _user_last_request[user_id] = now

    for url in supported[:3]:
        platform = identify_platform(url)

        status_msg = await message.reply_text(
            "🔍 Анализирую ссылку...", reply_to_message_id=message.message_id
        )

        try:
            info = await asyncio.to_thread(get_video_info, url)
        except DownloadError as e:
            await status_msg.edit_text(
                f"❌ <b>Не удалось получить данные</b>\n\n{_escape_html(str(e))}",
                parse_mode=ParseMode.HTML,
            )
            continue
        except Exception:
            logger.exception("Pre-check error for %s", url)
            info = None

        if info and is_live(info):
            await status_msg.edit_text(
                "📡 Это прямая трансляция. Скачиваются только записи, клипы и обычные видео."
            )
            continue

        size, exact = estimate_size(info) if info else (0, False)

        if size and not has_free_space(size):
            await status_msg.edit_text("❌ Недостаточно места на сервере для этого файла.")
            continue

        # Отсекаем заранее, только если размер точный (оценка по битрейту может врать)
        if exact and size > MAX_FILE_SIZE_BYTES:
            await status_msg.edit_text(
                f"❌ Файл слишком большой ({format_file_size(size)}), "
                f"лимит {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
            )
            continue

        sid = _store_url(url)

        if size > WARNING_THRESHOLD_BYTES:
            approx = "" if exact else "≈"
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Да", callback_data=f"conf|y|{sid}"),
                InlineKeyboardButton("Нет", callback_data=f"conf|n|{sid}"),
            ]])
            await status_msg.edit_text(
                f"⚠️ Файл больше {WARNING_THRESHOLD_BYTES // (1024 * 1024)} MB "
                f"({approx}{format_file_size(size)}). Продолжить?",
                reply_markup=keyboard,
            )
            continue

        # Переиспользуем сообщение, а не delete + новое
        await status_msg.edit_text(
            _format_prompt(platform), reply_markup=_format_keyboard(sid), parse_mode=ParseMode.HTML
        )


# ───────────────────────────── callbacks ─────────────────────────────

async def _send_video(query, prepared, caption: str, duration: int, reply_to: int | None) -> None:
    meta = prepared.meta
    kwargs = {}
    if meta.width and meta.height:
        kwargs["width"] = meta.width
        kwargs["height"] = meta.height
    if prepared.thumbnail:
        # Превью всегда multipart (bytes) — работает и в local, и в облачном режиме
        kwargs["thumbnail"] = prepared.thumbnail.read_bytes()

    with contextlib.ExitStack() as stack:
        await query.message.chat.send_video(
            video=_media_input(prepared.path, stack),
            caption=caption,
            duration=duration or None,
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
            reply_to_message_id=reply_to,
            allow_sending_without_reply=True,
            read_timeout=UPLOAD_TIMEOUT,
            write_timeout=UPLOAD_TIMEOUT,
            **kwargs,
        )


async def _send_audio(query, path: Path, caption: str, result: dict, duration: int,
                      reply_to: int | None) -> None:
    with contextlib.ExitStack() as stack:
        await query.message.chat.send_audio(
            audio=_media_input(path, stack),
            caption=caption,
            title=(result.get("title") or "Audio")[:TITLE_LIMIT],
            performer=result.get("uploader") or None,
            duration=duration or None,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_to,
            allow_sending_without_reply=True,
            read_timeout=UPLOAD_TIMEOUT,
            write_timeout=UPLOAD_TIMEOUT,
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split("|")
    if len(parts) != 3:
        return
    kind, action, sid = parts

    # Подтверждение для больших файлов
    if kind == "conf":
        if action == "n":
            _get_url(sid, pop=True)
            await _safe_edit(query, "Отменено.")
            return
        url = _get_url(sid)
        if not url:
            await _safe_edit(query, "⚠️ This link has expired. Please send it again.")
            return
        await _safe_edit(query, _format_prompt(identify_platform(url) or "Unknown"),
                         reply_markup=_format_keyboard(sid), parse_mode=ParseMode.HTML)
        return

    if kind != "dl":
        return

    audio_only = action == "a"
    user_id = update.effective_user.id

    url = _get_url(sid, pop=True)  # pop защищает от двойного нажатия
    if not url:
        await _safe_edit(query, "⚠️ This link has expired. Please send it again.")
        return

    platform = identify_platform(url) or "Unknown"
    # Отвечаем на исходное сообщение пользователя, а не на удаляемое служебное
    original = query.message.reply_to_message
    reply_to = original.message_id if original else None

    await _safe_edit(
        query,
        f"⏳ Processing <b>{platform}</b>...\n"
        f"Format: {'🎵 Audio' if audio_only else '🎬 Video'}\n"
        "<i>Waiting for a download slot...</i>",
        parse_mode=ParseMode.HTML,
    )

    file_path: str | None = None
    acquired = False
    chat_action = ChatAction.UPLOAD_DOCUMENT if audio_only else ChatAction.UPLOAD_VIDEO

    try:
        await queue_manager.acquire(user_id)
        acquired = True
        stats.record_attempt()

        await _safe_edit(query, f"📥 Downloading from <b>{platform}</b>...", parse_mode=ParseMode.HTML)

        async with _chat_action(query.message.chat, chat_action):
            logger.info("Starting download: %s (audio_only=%s)", url, audio_only)
            result = await download_video_async(url, audio_only=audio_only)
            file_path = result["file_path"]
            duration = int(result.get("duration") or 0)

            if audio_only:
                size = get_file_size(file_path)
                await _safe_edit(query, "📤 Uploading...")
                await _send_audio(query, Path(file_path),
                                  _build_caption(result, platform, duration, size),
                                  result, duration, reply_to)
            else:
                await _safe_edit(query, "⚙️ Подготовка видео...")
                prepared = await asyncio.to_thread(prepare_video, file_path)
                file_path = str(prepared.path)
                duration = prepared.meta.duration or duration
                size = get_file_size(file_path)

                logger.info("Uploading %s (%s, %sx%s, codec=%s, thumb=%s)",
                            file_path, format_file_size(size), prepared.meta.width,
                            prepared.meta.height, prepared.meta.vcodec, bool(prepared.thumbnail))
                await _safe_edit(query, "📤 Uploading...")
                await _send_video(query, prepared,
                                  _build_caption(result, platform, duration, size),
                                  duration, reply_to)

        stats.record_success(platform, user_id)
        with contextlib.suppress(TelegramError):
            await query.delete_message()
        logger.info("Successfully sent to user %s", user_id)

    except FileTooLargeError as e:
        stats.record_too_large()
        await _safe_edit(query, f"❌ <b>Too Large</b>\n\n{_escape_html(str(e))}", parse_mode=ParseMode.HTML)
    except DownloadError as e:
        stats.record_failure()
        logger.error("Download error for %s: %s", url, e)
        await _safe_edit(query, f"❌ <b>Download Failed</b>\n\n{_escape_html(str(e))}", parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Unexpected error in callback for %s", url)
        stats.record_failure()
        await _safe_edit(query, "❌ <b>An unexpected error occurred.</b>", parse_mode=ParseMode.HTML)
    finally:
        if file_path:
            cleanup_file(file_path)
            logger.info("Cleanup performed for: %s", file_path)
        if acquired:
            await queue_manager.release(user_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любое необработанное исключение — в лог с трейсбеком и короткий ответ пользователю."""
    logger.error("Unhandled error while processing update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(TelegramError):
            await update.effective_message.reply_text("❌ Внутренняя ошибка, попробуйте ещё раз.")


# ─────────────────────── Handler Registration ────────────────────

def get_handlers() -> list:
    return [
        CommandHandler("start", start_command),
        CommandHandler("id", id_command),
        CommandHandler("help", help_command),
        CommandHandler("status", status_command),
        CommandHandler("stats", stats_command),
        # CAPTION — ссылки в подписях к пересланным видео/фото
        MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message),
        CallbackQueryHandler(handle_callback),
    ]

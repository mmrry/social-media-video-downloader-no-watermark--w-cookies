import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
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
    PENDING_URL_TTL,
)
from bot.downloader import (
    download_video_async,
    get_video_info,
    has_free_space,
    is_live,
    first_entry,
    cleanup_file,
    DownloadError,
    FileTooLargeError,
)
from bot.media import prepare_video, make_cover, ratio_label
from bot.formats import (QualityOption, build_options, ensure_dimensions, source_dims,
                         available_ratios, ORIENT_NAME)
from bot.utils import extract_urls, identify_platform, format_file_size, get_file_size, _escape_html
from bot.stats import stats
from bot import queue_manager

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TITLE_LIMIT = 200
# В local mode запрос короткий, но сервер отвечает только после загрузки в Telegram
UPLOAD_TIMEOUT = 1800

_user_last_request: dict[int, float] = {}

@dataclass
class _Pending:
    url: str
    platform: str
    title: str
    options: list[QualityOption]
    duration: int = 0
    hidden: int = 0
    dims: tuple[int, int] = (0, 0)
    ratios: list = field(default_factory=list)
    created: float = field(default_factory=time.monotonic)


# short_id -> варианты выбора. Короткий id обходит лимит callback_data в 64 байта.
_pending: dict[str, _Pending] = {}


def _store_pending(item: _Pending) -> str:
    now = time.monotonic()
    for k in [k for k, v in _pending.items() if now - v.created > PENDING_URL_TTL]:
        _pending.pop(k, None)
    sid = uuid.uuid4().hex[:8]
    _pending[sid] = item
    return sid


def _get_pending(sid: str, pop: bool = False) -> _Pending | None:
    # pop на финальном шаге защищает от двойного нажатия
    item = _pending.pop(sid, None) if pop else _pending.get(sid)
    if item and time.monotonic() - item.created > PENDING_URL_TTL:
        _pending.pop(sid, None)
        return None
    return item


def _quality_keyboard(sid: str, options: list[QualityOption]) -> InlineKeyboardMarkup:
    video = [o for o in options if not o.audio_only]
    audio = [o for o in options if o.audio_only]
    idx = {id(o): i for i, o in enumerate(options)}
    rows = []
    # По одному в строке: «1080p60 · 16:9 · 324.0 MB» не влезает по два на телефоне
    for o in video:
        rows.append([InlineKeyboardButton(o.label, callback_data=f"q|{sid}|{idx[id(o)]}")])
    for o in audio:
        rows.append([InlineKeyboardButton(o.label, callback_data=f"q|{sid}|{idx[id(o)]}")])
    rows.append([InlineKeyboardButton("✖️ Отмена", callback_data=f"x|{sid}|0")])
    return InlineKeyboardMarkup(rows)


def _quality_prompt(platform: str, title: str, duration: int, hidden: int,
                    dims: tuple[int, int] = (0, 0),
                    ratios: list[tuple[str, str]] | None = None) -> str:
    text = f"🎯 <b>{platform}</b>"
    if title:
        short = title if len(title) <= 120 else title[:119] + "…"
        text += f"\n{_escape_html(short)}"
    if duration:
        m, s_ = divmod(int(duration), 60)
        h, m = divmod(m, 60)
        text += f"\n⏱ {h}:{m:02d}:{s_:02d}" if h else f"\n⏱ {m}:{s_:02d}"
    w, h = dims
    if ratios and len(ratios) > 1:
        # Площадка отдаёт несколько версий кадра (Twitch: обычная + вертикальная)
        text += "\n📐 Есть версии: " + " и ".join(
            f"{r} ({ORIENT_NAME.get(o, '?')})" for r, o in ratios)
    elif w and h:
        orient = "вертикальное" if h > w else "горизонтальное" if w > h else "квадратное"
        text += f"\n📐 {ratio_label(w, h)} · {orient} · {w}×{h}"
    text += "\n\nВыберите качество:"
    if hidden:
        text += (f"\n<i>Скрыто вариантов: {hidden} — больше лимита "
                 f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB</i>")
    return text


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


def _size_hint(option: QualityOption) -> str:
    if not option.size:
        return ""
    return f" ({'' if option.exact else '≈'}{format_file_size(option.size)})"


def _build_caption(result: dict, platform: str, duration: int, file_size: int,
                   dims: tuple[int, int] = (0, 0)) -> str:
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
    if dims[0] and dims[1]:
        caption += f"  📐 {ratio_label(*dims)} · {dims[0]}×{dims[1]}"
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
        "Just send me a link and pick the quality — every option shows its file size, or choose <b>MP3</b>.\n\n"
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
        "3. Pick the quality (resolution + size) or MP3.",
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

        # Размеры кадра: из метаданных, а если площадка их не отдала — ffprobe по потоку
        if info:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(ensure_dimensions, info)
        options, hidden = build_options(info, MAX_FILE_SIZE_BYTES)
        dims = source_dims(info) if info else (0, 0)
        if not options:
            await status_msg.edit_text(
                f"❌ Файл слишком большой: все варианты больше лимита "
                f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
            )
            continue

        entry = first_entry(info) or {}
        sid = _store_pending(_Pending(
            url=url, platform=platform, title=entry.get("title") or "", options=options,
            duration=int(entry.get("duration") or 0), hidden=hidden, dims=dims,
            ratios=available_ratios(options),
        ))
        # Переиспользуем сообщение, а не delete + новое
        await status_msg.edit_text(
            _quality_prompt(platform, entry.get("title") or "", entry.get("duration") or 0, hidden, dims,
                            available_ratios(options)),
            reply_markup=_quality_keyboard(sid, options),
            parse_mode=ParseMode.HTML,
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


# Эти форматы Telegram показывает в аудиоплеере; остальное (wav, flac, opus...) — документом
_TG_AUDIO_EXTS = {".mp3", ".m4a"}


async def _send_audio(query, path: Path, caption: str, result: dict, duration: int,
                      reply_to: int | None) -> None:
    cover = await asyncio.to_thread(make_cover, result.get("cover"))
    common = dict(
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_to_message_id=reply_to,
        allow_sending_without_reply=True,
        read_timeout=UPLOAD_TIMEOUT,
        write_timeout=UPLOAD_TIMEOUT,
    )
    if cover:
        common["thumbnail"] = cover
    with contextlib.ExitStack() as stack:
        media = _media_input(path, stack)
        if path.suffix.lower() in _TG_AUDIO_EXTS:
            await query.message.chat.send_audio(
                audio=media,
                title=(result.get("title") or "Audio")[:TITLE_LIMIT],
                performer=result.get("artist") or result.get("uploader") or None,
                duration=duration or None,
                **common,
            )
        else:
            await query.message.chat.send_document(document=media, **common)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split("|")
    if len(parts) != 3 or parts[0] not in ("q", "x"):
        return
    kind, sid, raw_idx = parts

    # pop защищает от двойного нажатия
    item = _get_pending(sid, pop=True)
    if kind == "x":
        await _safe_edit(query, "Отменено.")
        return
    if not item:
        await _safe_edit(query, "⚠️ Выбор устарел. Отправьте ссылку ещё раз.")
        return
    try:
        option = item.options[int(raw_idx)]
    except (ValueError, IndexError):
        await _safe_edit(query, "⚠️ Неизвестный вариант. Отправьте ссылку ещё раз.")
        return

    url, platform = item.url, item.platform
    audio_only = option.audio_only
    user_id = update.effective_user.id
    choice = option.short

    if option.size and not has_free_space(option.size):
        await _safe_edit(query, "❌ Недостаточно места на сервере для этого варианта.")
        return

    # Отвечаем на исходное сообщение пользователя, а не на удаляемое служебное
    original = query.message.reply_to_message
    reply_to = original.message_id if original else None

    await _safe_edit(
        query,
        f"⏳ <b>{platform}</b> · {choice}{_size_hint(option)}\n"
        "<i>Ожидание слота загрузки...</i>",
        parse_mode=ParseMode.HTML,
    )

    file_path: str | None = None
    acquired = False
    chat_action = ChatAction.UPLOAD_DOCUMENT if audio_only else ChatAction.UPLOAD_VIDEO

    try:
        await queue_manager.acquire(user_id)
        acquired = True
        stats.record_attempt()

        await _safe_edit(query, f"📥 Скачиваю <b>{platform}</b> · {choice}{_size_hint(option)}...",
                         parse_mode=ParseMode.HTML)

        async with _chat_action(query.message.chat, chat_action):
            logger.info("Starting download: %s (%s, selector=%s)", url, choice, option.selector)
            result = await download_video_async(url, audio_only=audio_only,
                                                format_selector=option.selector,
                                                audio_format=option.audio_format)
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
                                  _build_caption(result, platform, duration, size,
                                                 (prepared.meta.width, prepared.meta.height)),
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

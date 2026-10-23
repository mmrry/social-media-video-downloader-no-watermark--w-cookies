import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat, MessageEntity,
                      InputMediaPhoto, InputMediaVideo, InputMediaDocument)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
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
    SLIDESHOW_AUDIO,
)
from bot import errors
from bot.downloader import (
    download_video_async,
    download_gallery,
    probe_media,
    cleanup_by_id,
    has_free_space,
    is_live,
    first_entry,
    rejection_reason,
    cleanup_file,
    DownloadError,
    FileTooLargeError,
)
from bot.gallery import Gallery
from bot.media import prepare_video, prepare_photo, make_cover, ratio_label, PreparedVideo
from bot.formats import (QualityOption, build_options, ensure_dimensions, source_dims, MP3_BITRATE_KBPS,
                         available_ratios, ORIENT_NAME, ORIENT_ICON)
from bot.utils import extract_urls, identify_platform, format_file_size, get_file_size, _escape_html
from bot.stats import stats
from bot import activity
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
    music: bool = False   # MP3 музыки слайд-шоу: теги и подпись — по названию трека
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


# С какого количества вариантов раскладывать кнопки в 2 столбца
TWO_COLUMNS_FROM = 5


def _quality_keyboard(sid: str, options: list[QualityOption]) -> InlineKeyboardMarkup:
    two_columns = len(options) >= TWO_COLUMNS_FROM
    rows: list[list[InlineKeyboardButton]] = []

    # Последовательные блоки одной группы (🎬 горизонтальные, 📱 вертикальные, 🎵 аудио):
    # кнопки разных групп в одну строку не попадают
    blocks: list[list[tuple[int, QualityOption]]] = []
    for i, o in enumerate(options):
        if blocks and blocks[-1][0][1].group == o.group:
            blocks[-1].append((i, o))
        else:
            blocks.append([(i, o)])

    per_row = 2 if two_columns else 1
    for block in blocks:
        for j in range(0, len(block), per_row):
            rows.append([
                InlineKeyboardButton(o.compact if two_columns and o.compact else o.label,
                                     callback_data=f"q|{sid}|{i}")
                for i, o in block[j:j + per_row]
            ])
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
            f"{ORIENT_ICON.get(o, '🎬')} {r} ({ORIENT_NAME.get(o, '?')})" for r, o in ratios)
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
        "📸 Photo posts and carousels from Instagram and TikTok are sent as an album.\n\n"
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
        "4. Wait for the file to be processed.",
        "📸 Instagram / TikTok photo posts and carousels arrive as an album — no quality choice needed.\n",
        "<b>Supported Platforms:</b>",
    ]
    lines += [f"• {p}" for p in sorted(SUPPORTED_PLATFORMS.keys())]
    lines += ["\n<b>Commands:</b>", "/status - Check bot load & queue", "/stats - Global download statistics"]
    if update.effective_user.id in ADMIN_IDS:
        lines.append("/report - Today's activity report (admins)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🛰 <b>Bot Status</b>\n\n"
        f"Active downloads: <b>{queue_manager.active_downloads()}</b>\n"
        f"Waiting in queue: <b>{queue_manager.queue_depth()}</b>\n\n"
        "✅ The bot is running normally."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ───────────────────────────── отчёт админам ─────────────────────────────

# Создаётся в main.py при DAILY_REPORT=1; None — отчёт выключен
activity_log: "activity.ActivityLog | None" = None
REPORT_TZ_NAME = "UTC"


def _record_activity(update: Update, urls: list[str]) -> None:
    if activity_log is None:
        return
    for url in urls[:10]:   # не больше 10 ссылок из одного сообщения
        activity_log.record(update.effective_user, update.effective_chat, url, identify_platform(url))


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/report — отчёт за сегодня на текущий момент (только админам)."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🔒 This command is restricted to admins.")
        return
    if activity_log is None:
        await update.message.reply_text("Отчёт выключен. Включите DAILY_REPORT=1 в .env и перезапустите бота.")
        return
    day = activity_log.today()
    report = activity.build_report(day, activity_log.load(day), REPORT_TZ_NAME, activity_log.overflow(day))
    await activity.send_report(context.bot, [update.effective_user.id], report, day)


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
    _record_activity(update, urls)
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

        if reason := rejection_reason(url):
            await message.reply_text(f"🚫 {reason}", reply_to_message_id=message.message_id)
            continue

        status_msg = await message.reply_text(
            "🔍 Анализирую ссылку...", reply_to_message_id=message.message_id
        )

        try:
            info = await asyncio.to_thread(probe_media, url)
        except DownloadError as e:
            await status_msg.edit_text(
                f"❌ <b>Не удалось получить данные</b>\n\n{_escape_html(str(e))}",
                parse_mode=ParseMode.HTML,
            )
            continue
        except Exception:
            logger.exception("Pre-check error for %s", url)
            info = None

        if isinstance(info, Gallery):
            # Фото/карусель: выбирать нечего — сразу качаем и отправляем альбомом
            await _process_gallery(message, status_msg, url, platform, info, user_id)
            continue

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


async def _send_audio(chat: Chat, path: Path, caption: str, result: dict, duration: int,
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
            await chat.send_audio(
                audio=media,
                title=(result.get("title") or "Audio")[:TITLE_LIMIT],
                performer=result.get("artist") or result.get("uploader") or None,
                duration=duration or None,
                **common,
            )
        else:
            await chat.send_document(document=media, **common)


# ───────────────────────────── gallery (фото-посты) ─────────────────────────────

# Telegram: в одном альбоме 2–10 элементов
ALBUM_SIZE = 10


@dataclass
class _GalleryMedia:
    kind: str                       # "photo" | "video"
    path: Path
    video: PreparedVideo | None = None


def _media_counts(photos: int, videos: int) -> str:
    parts = []
    if photos:
        parts.append(f"🖼 {photos} фото")
    if videos:
        parts.append(f"🎬 {videos} видео")
    return " · ".join(parts)


def _gallery_caption(g: Gallery, platform: str, media: list[_GalleryMedia], failed: int) -> str:
    text = (g.description or g.title or "").strip()
    if len(text) > TITLE_LIMIT:
        text = text[:TITLE_LIMIT - 1] + "…"
    lines = []
    if text:
        lines.append(f"<b>{_escape_html(text)}</b>")
    if g.uploader:
        lines.append(f"👤 {_escape_html(g.uploader)}")
    photos = sum(1 for m in media if m.kind == "photo")
    lines.append(f"📱 {platform} · {_media_counts(photos, len(media) - photos)}")
    if g.total > len(g.items):
        lines.append(f"<i>Показаны первые {len(g.items)} из {g.total}</i>")
    if failed:
        lines.append(f"<i>Не удалось загрузить: {failed}</i>")
    return "\n".join(lines)[:CAPTION_LIMIT]


def _prepare_gallery(files) -> list[_GalleryMedia]:
    """Синхронно (to_thread): фото -> JPEG/PNG в лимитах Telegram, видео -> faststart/превью."""
    out = []
    for f in files:
        if f.kind == "photo":
            out.append(_GalleryMedia("photo", prepare_photo(f.path)))
        else:
            prepared = prepare_video(str(f.path))
            out.append(_GalleryMedia("video", prepared.path, prepared))
    return out


def _input_media(m: _GalleryMedia, stack: contextlib.ExitStack, caption: str | None,
                 as_document: bool):
    # PTB превращает Path в InputMedia* всегда в file:// — в облачном режиме нужен открытый файл
    media = _media_input(m.path, stack)
    common = {"caption": caption, "parse_mode": ParseMode.HTML if caption else None}
    if as_document:
        return InputMediaDocument(media, **common)
    if m.kind == "photo":
        return InputMediaPhoto(media, **common)
    meta = m.video.meta if m.video else None
    extra = {}
    if meta and meta.width and meta.height:
        extra.update(width=meta.width, height=meta.height)
    if meta and meta.duration:
        extra["duration"] = meta.duration
    if m.video and m.video.thumbnail:
        extra["thumbnail"] = m.video.thumbnail.read_bytes()
    return InputMediaVideo(media, supports_streaming=True, **common, **extra)


async def _send_album(chat: Chat, chunk: list[_GalleryMedia], caption: str | None,
                      reply_to: int | None, as_document: bool) -> None:
    timeouts = dict(read_timeout=UPLOAD_TIMEOUT, write_timeout=UPLOAD_TIMEOUT)
    reply = dict(reply_to_message_id=reply_to, allow_sending_without_reply=True)
    for attempt in range(2):
        # Файлы открываются заново на каждую попытку: отправленный поток уже прочитан
        with contextlib.ExitStack() as stack:
            try:
                if len(chunk) > 1:
                    items = [_input_media(m, stack, caption if i == 0 else None, as_document)
                             for i, m in enumerate(chunk)]
                    await chat.send_media_group(media=items, **reply, **timeouts)
                    return
                m = chunk[0]
                file = _media_input(m.path, stack)
                kw = {"caption": caption, "parse_mode": ParseMode.HTML, **reply, **timeouts}
                if as_document:
                    await chat.send_document(document=file, **kw)
                elif m.kind == "photo":
                    await chat.send_photo(photo=file, **kw)
                else:
                    meta = m.video.meta if m.video else None
                    extra = {}
                    if meta and meta.width and meta.height:
                        extra.update(width=meta.width, height=meta.height)
                    if m.video and m.video.thumbnail:
                        extra["thumbnail"] = m.video.thumbnail.read_bytes()
                    await chat.send_video(video=file, duration=(meta.duration if meta else None) or None,
                                          supports_streaming=True, **kw, **extra)
                return
            except RetryAfter as e:
                if attempt:
                    raise
                ra = e.retry_after
                await asyncio.sleep((ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)) + 1)


async def _send_gallery(chat: Chat, media: list[_GalleryMedia], caption: str,
                        reply_to: int | None) -> None:
    """Альбомами по 10, подпись — у первого элемента. Если Telegram не принял
    альбом как фото/видео (размеры, формат) — повторяем его документами."""
    chunks = [media[i:i + ALBUM_SIZE] for i in range(0, len(media), ALBUM_SIZE)]
    for n, chunk in enumerate(chunks):
        cap = caption if n == 0 else None
        if n:
            await asyncio.sleep(1)  # не упираться в flood-лимит при нескольких альбомах
        try:
            await _send_album(chat, chunk, cap, reply_to, as_document=False)
        except BadRequest as e:
            logger.warning("Album rejected (%s), resending as documents", e)
            await _send_album(chat, chunk, cap, reply_to, as_document=True)


# ───────────── музыка слайд-шоу TikTok ─────────────

# Селектор музыки: в ответе TikTok у слайд-шоу это единственный аудиоформат «audio»
_MUSIC_SELECTOR = "audio/bestaudio/best"


def _music_result(result: dict) -> dict:
    """Для MP3 слайд-шоу: название трека и исполнитель вместо подписи поста."""
    return {**result,
            "title": result.get("track") or result.get("title") or "Audio",
            "uploader": result.get("artist") or result.get("uploader")}


def _music_name(g: Gallery) -> str:
    return " — ".join(x for x in (g.track, g.artist) if x)


async def _offer_music(message, url: str, platform: str, g: Gallery) -> None:
    """Кнопка «🎵 Скачать музыку (MP3)» после альбома — тот же поток, что MP3 у видео."""
    size = int(MP3_BITRATE_KBPS * 1000 / 8 * g.audio_duration) if g.audio_duration else 0
    label = "🎵 Скачать музыку · MP3" + (f" · ≈{format_file_size(size)}" if size else "")
    option = QualityOption(label, _MUSIC_SELECTOR, True, size, False, audio_format="mp3",
                           compact=label, group="a")
    sid = _store_pending(_Pending(url=url, platform=platform, title=g.track, options=[option],
                                  duration=g.audio_duration, music=True))
    text = "🎵 <b>Музыка из слайд-шоу</b>"
    if name := _music_name(g):
        text += f"\n{_escape_html(name)}"
    if g.audio_duration:
        m, sec = divmod(g.audio_duration, 60)
        text += f"\n⏱ {m}:{sec:02d}"
    await message.reply_text(text, reply_markup=_quality_keyboard(sid, [option]),
                             parse_mode=ParseMode.HTML, reply_to_message_id=message.message_id)


async def _send_music(message, url: str, platform: str) -> None:
    """SLIDESHOW_AUDIO=auto: MP3 сразу после альбома (в том же слоте загрузки)."""
    file_path = None
    try:
        result = await download_video_async(url, audio_only=True, format_selector=_MUSIC_SELECTOR,
                                            audio_format="mp3")
        file_path = result["file_path"]
        result = _music_result(result)
        duration = int(result.get("duration") or 0)
        await _send_audio(message.chat, Path(file_path),
                          _build_caption(result, platform, duration, get_file_size(file_path)),
                          result, duration, message.message_id)
    except Exception as e:  # noqa: BLE001 — альбом уже отправлен, музыка — дополнение
        logger.warning("Slideshow audio failed for %s: %s", url, e)
        with contextlib.suppress(TelegramError):
            await message.reply_text("🎵 Музыку из слайд-шоу скачать не удалось.",
                                     reply_to_message_id=message.message_id)
    finally:
        if file_path:
            cleanup_file(file_path)


async def _process_gallery(message, status_msg, url: str, platform: str, gallery: Gallery,
                           user_id: int) -> None:
    counts = _media_counts(gallery.photos, gallery.videos)
    with contextlib.suppress(TelegramError):
        await status_msg.edit_text(f"⏳ <b>{platform}</b> · {counts}\n<i>Ожидание слота загрузки...</i>",
                                   parse_mode=ParseMode.HTML)
    acquired = False
    download = None
    try:
        await queue_manager.acquire(user_id)
        acquired = True
        stats.record_attempt()
        with contextlib.suppress(TelegramError):
            await status_msg.edit_text(f"📥 Скачиваю <b>{platform}</b> · {counts}...",
                                       parse_mode=ParseMode.HTML)

        async with _chat_action(message.chat, ChatAction.UPLOAD_PHOTO):
            download = await asyncio.to_thread(download_gallery, url, gallery)
            media = await asyncio.to_thread(_prepare_gallery, download.files)
            with contextlib.suppress(TelegramError):
                await status_msg.edit_text("📤 Uploading...")
            await _send_gallery(message.chat, media,
                                _gallery_caption(gallery, platform, media, download.failed),
                                message.message_id)

        stats.record_success(platform, user_id)
        with contextlib.suppress(TelegramError):
            await status_msg.delete()
        logger.info("Gallery sent to user %s: %d items (%d failed)", user_id,
                    len(download.files), download.failed)

        if gallery.audio and SLIDESHOW_AUDIO != "off":
            # Развёрнутая ссылка поста: короткую vm.tiktok.com штатный экстрактор
            # при повторном запросе отправит в TikTokIE, который не знает /photo/
            post_url = gallery.webpage_url or url
            if SLIDESHOW_AUDIO == "auto":
                async with _chat_action(message.chat, ChatAction.UPLOAD_DOCUMENT):
                    await _send_music(message, post_url, platform)
            else:
                with contextlib.suppress(TelegramError):
                    await _offer_music(message, post_url, platform, gallery)
    except DownloadError as e:
        stats.record_failure()
        logger.error("Gallery error for %s: %s", url, e)
        with contextlib.suppress(TelegramError):
            await status_msg.edit_text(f"❌ <b>Не удалось скачать</b>\n\n{_escape_html(str(e))}",
                                       parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Unexpected gallery error for %s", url)
        stats.record_failure()
        with contextlib.suppress(TelegramError):
            await status_msg.edit_text(f"❌ <b>Не удалось отправить публикацию</b>\n\n{errors.INTERNAL}",
                                       parse_mode=ParseMode.HTML)
    finally:
        if download:
            cleanup_by_id(download.file_id)
        if acquired:
            await queue_manager.release(user_id)


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
                if item.music:
                    result = _music_result(result)
                await _send_audio(query.message.chat, Path(file_path),
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
        await _safe_edit(query, f"❌ <b>Файл слишком большой</b>\n\n{_escape_html(str(e))}",
                         parse_mode=ParseMode.HTML)
    except DownloadError as e:
        stats.record_failure()
        logger.error("Download error for %s: %s", url, e)
        await _safe_edit(query, f"❌ <b>Не удалось скачать</b>\n\n{_escape_html(str(e))}",
                         parse_mode=ParseMode.HTML)
    except BadRequest as e:
        stats.record_failure()
        logger.error("Telegram rejected file for %s: %s", url, e)
        await _safe_edit(query, "❌ <b>Не удалось отправить файл</b>\n\nTelegram не принял файл.",
                         parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Unexpected error in callback for %s", url)
        stats.record_failure()
        await _safe_edit(query, f"❌ <b>Не удалось скачать</b>\n\n{errors.INTERNAL}", parse_mode=ParseMode.HTML)
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
        CommandHandler("report", report_command),
        # CAPTION — ссылки в подписях к пересланным видео/фото
        MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message),
        CallbackQueryHandler(handle_callback),
    ]

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

import yt_dlp

from bot.config import (
    DOWNLOAD_DIR,
    MAX_FILE_SIZE_BYTES,
    COOKIES_FILE,
    VK_COOKIES_FILE,
    DISK_RESERVE_BYTES,
    PREFER_H264,
    PROXY,
    VK_PROXY,
    FORCE_IPV4,
    VK_API_HOST,
    SOCKET_TIMEOUT,
)
from bot.vk_live import VKVideoLiveClipIE
from bot.ytdlp_patches import apply_vk_host_patch

logger = logging.getLogger(__name__)

apply_vk_host_patch(VK_API_HOST)

_VK_DOMAINS = ("vk.com", "vk.ru", "vkvideo.ru")

_ID_RE = re.compile(r"^[0-9a-f]{12}\.")
_SIDECAR_SUFFIXES = (".part", ".ytdl", ".jpg", ".jpeg", ".webp", ".png", ".json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.5",
    "Sec-Fetch-Mode": "navigate",
}


class DownloadError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


# ───────────────────────────── helpers ─────────────────────────────

def has_free_space(required_bytes: int) -> bool:
    """Свободное место в DOWNLOAD_DIR с учётом резерва."""
    try:
        return shutil.disk_usage(DOWNLOAD_DIR).free > required_bytes + DISK_RESERVE_BYTES
    except OSError:
        return True  # Fallback


def _clean_error(e: Exception) -> str:
    msg = str(e).split(";")[0]
    return msg.replace("ERROR: ", "", 1).strip()


def first_entry(info: dict | None) -> dict | None:
    """Для плейлистов/каруселей берём первый элемент."""
    while info and info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else None
    return info


def is_live(info: dict | None) -> bool:
    """Идущий или запланированный эфир — скачивать его бесконечно нельзя."""
    entry = first_entry(info) or {}
    return bool(entry.get("is_live")) or entry.get("live_status") in ("is_live", "is_upcoming")


def _extract(ydl: yt_dlp.YoutubeDL, url: str, download: bool) -> dict | None:
    """
    extract_info с поддержкой собственных экстракторов.
    add_info_extractor добавляет IE в конец списка (после generic),
    поэтому для своих URL передаём ie_key явно.
    """
    if VKVideoLiveClipIE.suitable(url):
        ydl.add_info_extractor(VKVideoLiveClipIE())
        return ydl.extract_info(url, download=download, ie_key=VKVideoLiveClipIE.ie_key())
    return ydl.extract_info(url, download=download)


# (домены, файл cookies, обязательны ли cookies)
_COOKIE_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("instagram.com",), COOKIES_FILE, True),
    # Обычный VK (клипы, видео). Live-клипам cookies не нужны.
    (("vk.com", "vk.ru", "vkvideo.ru"), VK_COOKIES_FILE, False),
)


def _cookie_source(url: str) -> tuple[str, bool] | None:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if host.startswith("live."):  # live.vkvideo.ru — публичный API без авторизации
        return None
    for domains, path, required in _COOKIE_RULES:
        if any(host == d or host.endswith(f".{d}") for d in domains):
            return path, required
    return None


@contextmanager
def _cookie_file(url: str) -> Iterator[str | None]:
    """
    Отдаём yt-dlp временную КОПИЮ cookies: yt-dlp перезаписывает cookiefile
    при закрытии, и параллельные загрузки могут испортить общий файл.
    Заодно оригинал можно монтировать read-only.
    """
    rule = _cookie_source(url)
    if not rule:
        yield None
        return
    path, required = rule
    src = Path(path) if path else None
    if not src or not src.is_file() or src.stat().st_size == 0:
        if required:
            logger.warning("Cookies file not found for %s (%s)", url, path or "not configured")
        yield None
        return
    fd, tmp = tempfile.mkstemp(prefix="cookies_", suffix=".txt")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        yield tmp
    finally:
        Path(tmp).unlink(missing_ok=True)


def _proxy_for(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    is_vk = any(host == d or host.endswith(f".{d}") for d in _VK_DOMAINS) and not host.startswith("live.")
    return (VK_PROXY if is_vk and VK_PROXY else PROXY) or ""


def _base_opts(url: str, audio_only: bool, cookiefile: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "noplaylist": True,          # ссылка watch?v=..&list=.. -> только видео
        "playlist_items": "1",       # плейлист/карусель -> только первый элемент
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 10,
        "fragment_retries": 15,
        "geo_bypass": True,
        "http_headers": HEADERS,
        "extractor_args": {
            "tiktok": {"api_hostname": ["api22-normal-c-useast2a.tiktokv.com"]},
        },
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    proxy = _proxy_for(url)
    if proxy:
        opts["proxy"] = proxy
    if FORCE_IPV4:
        opts["source_address"] = "0.0.0.0"

    if audio_only:
        opts["format"] = "bestaudio/best"
    else:
        opts["format"] = "bestvideo*+bestaudio/best"
        if PREFER_H264:
            # H.264 + AAC -> Telegram сам строит превью и стримит во всех клиентах
            opts["format_sort"] = ["vcodec:h264", "res", "fps", "acodec:m4a"]
        opts["merge_output_format"] = "mp4"
    return opts


# ───────────────────────────── info / size ─────────────────────────────

def get_video_info(url: str) -> dict:
    """Метаданные без скачивания, с тем же выбором формата, что и при загрузке."""
    try:
        with _cookie_file(url) as cookiefile:
            opts = {**_base_opts(url, False, cookiefile), "quiet": True, "no_warnings": True,
                    "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = _extract(ydl, url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean_error(e)) from e
    if not info:
        raise DownloadError("Could not extract video information.")
    return info


def estimate_size(info: dict) -> tuple[int, bool]:
    """
    Оценка размера итогового файла (видео+аудио).
    Возвращает (bytes, exact): exact=False, если это приблизительная оценка.
    """
    entry = first_entry(info) or {}
    duration = entry.get("duration") or 0
    formats = entry.get("requested_formats") or [entry]

    total, exact = 0, True
    for f in formats:
        size = f.get("filesize")
        if not size:
            exact = False
            size = f.get("filesize_approx")
        if not size:
            tbr = f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0))
            size = tbr * 1000 / 8 * duration if tbr and duration else 0
        total += int(size or 0)

    if total == 0:
        exact = False
        if duration:
            total = int(2500 * 1000 / 8 * duration)  # ~2.5 Mbit/s в среднем
    return total, exact and total > 0


# ───────────────────────────── download ─────────────────────────────

def _make_guard_hook() -> Callable[[dict], None]:
    """Защита в процессе скачивания (если оценка размера не сработала)."""
    checked: set[str] = set()

    def hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        if (d.get("downloaded_bytes") or 0) > MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(
                f"Файл превысил лимит {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB во время загрузки."
            )
        # Проверка места один раз на каждый фрагмент/формат, а не на каждый тик
        name = d.get("filename") or ""
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if total and name not in checked:
            checked.add(name)
            if not has_free_space(total):
                raise DownloadError("На сервере закончилось свободное место.")

    return hook


def _find_output(file_id: str, prefer: str) -> Path | None:
    candidates = sorted(
        p for p in DOWNLOAD_DIR.glob(f"{file_id}.*")
        if p.is_file() and not p.name.endswith(_SIDECAR_SUFFIXES)
    )
    for p in candidates:
        if prefer and p.suffix == prefer:
            return p
    return candidates[0] if candidates else None


def _find_cover(file_id: str) -> str | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = DOWNLOAD_DIR / f"{file_id}{ext}"
        if p.is_file():
            return str(p)
    return None


def audio_stream_count(path: Path | str) -> int | None:
    """Сколько аудиодорожек в файле (None — ffprobe не смог прочитать)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        return len([line for line in out.splitlines() if line.strip()])
    except (subprocess.SubprocessError, OSError):
        return None


def _has_separate_audio(entry: dict) -> bool:
    # acodec может быть None (HLS без CODECS: «audio-128000-Audio») — это тоже аудио
    return any(f.get("vcodec") == "none" and f.get("acodec") != "none"
               for f in entry.get("formats") or [])


def _describe_formats(entry: dict) -> str:
    """Какие форматы реально скачал yt-dlp — для лога."""
    fmts = entry.get("requested_formats") or [entry]
    return " + ".join(
        f"{f.get('format_id')}[v={f.get('vcodec')},a={f.get('acodec')},{f.get('protocol')}]"
        for f in fmts
    )


def _repair_missing_audio(url: str, path: Path, file_id: str) -> bool:
    """
    Страховка: в видео нет звука, хотя у источника есть аудиоформаты
    (типичный случай — HLS без CODECS, yt-dlp считает видеопоток «полным»).
    Докачиваем bestaudio и муксим: видео копией, звук -> AAC.
    """
    audio_tmpl = str(DOWNLOAD_DIR / f"{file_id}.afix.%(ext)s")
    try:
        with _cookie_file(url) as cookiefile:
            opts = _base_opts(url, True, cookiefile)
            opts.update({"format": "bestaudio/best[acodec!=none]", "outtmpl": audio_tmpl,
                         "postprocessors": [], "quiet": True, "no_warnings": True})
            with yt_dlp.YoutubeDL(opts) as ydl:
                _extract(ydl, url, download=True)
    except Exception as e:
        logger.warning("audio repair: bestaudio download failed: %s", e)
        return False

    audio = next((p for p in DOWNLOAD_DIR.glob(f"{file_id}.afix.*")
                  if not p.name.endswith((".part", ".ytdl"))), None)
    if not audio:
        logger.warning("audio repair: audio file not found")
        return False

    out = path.with_name(f"{file_id}.amux.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-i", str(audio),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
           "-shortest", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=True)
    except subprocess.CalledProcessError as e:
        logger.warning("audio repair: mux failed: %s", (e.stderr or "").strip()[-400:])
        out.unlink(missing_ok=True)
        return False
    except subprocess.SubprocessError as e:
        logger.warning("audio repair: mux failed: %s", e)
        out.unlink(missing_ok=True)
        return False
    finally:
        audio.unlink(missing_ok=True)

    final = path.with_suffix(".mp4")
    if final != path:
        path.unlink(missing_ok=True)
    out.replace(final)
    return True


def cleanup_by_id(file_id: str) -> None:
    for p in DOWNLOAD_DIR.glob(f"{file_id}.*"):
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed to remove %s: %s", p, e)


AUDIO_EXTS = {"mp3": ".mp3", "m4a": ".m4a"}


def download_video(
    url: str,
    audio_only: bool = False,
    format_selector: str | None = None,
    audio_format: str = "mp3",
) -> dict[str, Any]:
    """Синхронная загрузка (запускается в потоке)."""
    file_id = uuid.uuid4().hex[:12]
    try:
        with _cookie_file(url) as cookiefile:
            opts = _base_opts(url, audio_only, cookiefile)
            if format_selector:
                opts["format"] = format_selector  # выбор пользователя; format_sort остаётся для fallback'ов
            opts.update({
                "outtmpl": str(DOWNLOAD_DIR / f"{file_id}.%(ext)s"),
                "max_filesize": MAX_FILE_SIZE_BYTES,
                "progress_hooks": [_make_guard_hook()],
                "postprocessors": [],
                # Страховка: канал, который сейчас в эфире, не качаем бесконечно
                "match_filter": yt_dlp.utils.match_filter_func("!is_live"),
            })
            if audio_only:
                if audio_format in AUDIO_EXTS:
                    # Если исходник уже в нужном кодеке — yt-dlp копирует поток без перекодирования
                    opts["postprocessors"].append({
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_format,
                        "preferredquality": "192",
                    })
                # Теги title/artist в файл + обложка отдельным jpg для Telegram
                opts["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})
                opts["writethumbnail"] = True
                opts["postprocessors"].append(
                    {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = _extract(ydl, url, download=True)

        entry = first_entry(info)
        if entry is None:
            raise DownloadError("Could not extract video information.")

        path = _find_output(file_id, AUDIO_EXTS.get(audio_format, "") if audio_only else ".mp4")
        if path is None and is_live(entry):
            raise DownloadError("Это прямая трансляция — скачиваются только записи и клипы.")
        if path is None:
            # yt-dlp молча пропускает файл больше max_filesize
            size, _ = estimate_size(entry)
            if size > MAX_FILE_SIZE_BYTES:
                raise FileTooLargeError(
                    f"Файл ≈{size / 1024 / 1024:.0f} MB, лимит {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
                )
            raise DownloadError("Download finished but file not found.")

        audio_repaired = False
        if not audio_only:
            n_audio = audio_stream_count(path)
            logger.info("Downloaded %s: %s | audio streams: %s",
                        path.name, _describe_formats(entry), n_audio)
            if n_audio == 0:
                if _has_separate_audio(entry):
                    logger.warning("No audio in %s, but source has audio formats — repairing", path.name)
                    audio_repaired = _repair_missing_audio(url, path, file_id)
                    if audio_repaired:
                        path = path.with_suffix(".mp4")
                        logger.info("Audio repaired: %s (audio streams: %s)",
                                    path.name, audio_stream_count(path))
                else:
                    logger.info("No audio in %s and source has no separate audio formats "
                                "(видео, вероятно, без звука в оригинале)", path.name)

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(
                f"File is {file_size / (1024 * 1024):.1f} MB, which exceeds the "
                f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB Telegram limit."
            )

        return {
            "file_path": str(path),
            "title": entry.get("title") or "Video",
            "duration": entry.get("duration") or 0,
            "platform": entry.get("extractor_key") or "unknown",
            "uploader": entry.get("uploader") or entry.get("channel") or "Unknown",
            "thumbnail": entry.get("thumbnail"),
            "width": entry.get("width") or 0,
            "height": entry.get("height") or 0,
            "audio_only": audio_only,
            "audio_format": audio_format if audio_only else None,
            "audio_repaired": audio_repaired,
            "cover": _find_cover(file_id) if audio_only else None,
            "artist": entry.get("artist") or entry.get("uploader"),
        }

    except (FileTooLargeError, DownloadError):
        cleanup_by_id(file_id)
        raise
    except yt_dlp.utils.DownloadError as e:
        cleanup_by_id(file_id)
        cause = e.exc_info[1] if getattr(e, "exc_info", None) else None
        if isinstance(cause, (FileTooLargeError, DownloadError)):
            raise cause from None
        logger.error("yt-dlp error: %s", e)
        raise DownloadError(f"Platform error: {_clean_error(e)}") from e
    except Exception as e:
        cleanup_by_id(file_id)
        logger.exception("Unexpected downloader error")
        raise DownloadError(f"Technical error: {e}") from e


async def download_video_async(
    url: str, audio_only: bool = False, format_selector: str | None = None, audio_format: str = "mp3",
) -> dict[str, Any]:
    return await asyncio.to_thread(download_video, url, audio_only, format_selector, audio_format)


# ───────────────────────────── cleanup ─────────────────────────────

def cleanup_file(file_path: str | None) -> None:
    """Удалить скачанный файл и все sidecar-файлы (превью, .tg.mp4 и т.п.)."""
    if not file_path:
        return
    p = Path(file_path)
    file_id = p.name.split(".", 1)[0]
    if _ID_RE.match(p.name):
        cleanup_by_id(file_id)
    else:
        p.unlink(missing_ok=True)


def purge_stale_downloads(max_age_seconds: int = 0) -> int:
    """
    Удаляет остатки прошлых запусков (OOM / exit 137 / падение при upload).
    Трогает только файлы вида <12 hex>.* — чужие файлы не удаляются.
    """
    now = time.time()
    removed = 0
    for p in DOWNLOAD_DIR.iterdir():
        try:
            if p.is_file() and _ID_RE.match(p.name) and now - p.stat().st_mtime >= max_age_seconds:
                p.unlink()
                removed += 1
        except OSError as e:
            logger.warning("Failed to purge %s: %s", p, e)
    return removed

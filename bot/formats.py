"""
Варианты качества для выбора кнопками в Telegram.

Из списка форматов yt-dlp строим по одному варианту на каждое разрешение
(высоту): лучший видеоформат этой высоты + лучшая аудиодорожка, с оценкой
итогового размера. Селектор формата делается с fallback'ами — ссылки
форматов за время раздумий пользователя могут протухнуть, а id форматов
при повторном извлечении иногда меняются.
"""
from dataclasses import dataclass

from bot.config import PREFER_H264, WARNING_THRESHOLD_BYTES
from bot.downloader import first_entry, estimate_size
from bot.utils import format_file_size

MP3_BITRATE_KBPS = 192
MAX_VIDEO_OPTIONS = 6
# Оценка по битрейту может врать — отсекаем приблизительные размеры с запасом
APPROX_OVERSHOOT = 1.3


@dataclass
class QualityOption:
    label: str
    selector: str | None   # None -> формат по умолчанию из downloader
    audio_only: bool
    size: int = 0
    exact: bool = False
    height: int = 0

    @property
    def short(self) -> str:
        """Название для статусных сообщений: '720p', 'MP3', 'Лучшее'."""
        if self.audio_only:
            return "MP3"
        return f"{self.height}p" if self.height else "Лучшее качество"


def _fsize(f: dict, duration: float) -> tuple[int, bool]:
    if f.get("filesize"):
        return int(f["filesize"]), True
    if f.get("filesize_approx"):
        return int(f["filesize_approx"]), False
    tbr = f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0))
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration), False
    return 0, False


def _is_h264(f: dict) -> bool:
    return (f.get("vcodec") or "").lower().startswith(("avc", "h264"))


def _size_label(size: int, exact: bool) -> str:
    if not size:
        return ""
    return f" · {'' if exact else '≈'}{format_file_size(size)}"


def _fits(size: int, exact: bool, max_bytes: int) -> bool:
    if not size:
        return True  # размер неизвестен — пусть решит проверка при загрузке
    return size <= (max_bytes if exact else max_bytes * APPROX_OVERSHOOT)


def build_options(info: dict | None, max_bytes: int) -> tuple[list[QualityOption], int]:
    """Возвращает (варианты, сколько разрешений скрыто из-за лимита размера)."""
    entry = first_entry(info) or {}
    duration = entry.get("duration") or 0
    formats = entry.get("formats") or []

    videos = [
        f for f in formats
        if f.get("vcodec") != "none"
        and (f.get("height") or 0) > 0
        and f.get("ext") != "mhtml"            # storyboard'ы YouTube
        and "storyboard" not in (f.get("format_note") or "")
    ]
    audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_audio = max(
        audios,
        # m4a/AAC — родной для MP4 и Telegram, затем битрейт
        key=lambda f: (f.get("ext") in ("m4a", "mp4"), f.get("abr") or f.get("tbr") or 0),
        default=None,
    )
    audio_size, audio_exact = _fsize(best_audio, duration) if best_audio else (0, True)

    # Лучший формат на каждую высоту
    best_by_height: dict[int, dict] = {}
    for f in videos:
        h = int(f["height"])
        key = (
            _is_h264(f) if PREFER_H264 else False,  # H.264 -> без транскода, с превью
            f.get("acodec") not in (None, "none"),  # уже со звуком
            f.get("fps") or 0,
            f.get("tbr") or f.get("vbr") or 0,
        )
        cur = best_by_height.get(h)
        if cur is None or key > cur[0]:
            best_by_height[h] = (key, f)

    options: list[QualityOption] = []
    hidden = 0
    for h in sorted(best_by_height, reverse=True):
        f = best_by_height[h][1]
        fid = f.get("format_id")
        size, exact = _fsize(f, duration)
        needs_audio = f.get("acodec") == "none" and best_audio is not None
        if needs_audio:
            size += audio_size
            exact = exact and audio_exact
            primary = f"{fid}+{best_audio.get('format_id')}"
        else:
            primary = fid
        if not _fits(size, exact, max_bytes):
            hidden += 1
            continue

        fps = int(f.get("fps") or 0)
        name = f"{h}p{fps if fps > 30 else ''}"
        warn = "⚠️ " if size > WARNING_THRESHOLD_BYTES else ""
        selector = (
            f"{primary}/bv*[height={h}]+ba/b[height={h}]"
            f"/bv*[height<={h}]+ba/b[height<={h}]/b"
        )
        options.append(QualityOption(f"{warn}🎬 {name}{_size_label(size, exact)}",
                                     selector, False, size, exact, h))
        if len(options) >= MAX_VIDEO_OPTIONS:
            break

    if not options and not videos:
        # Сайт не отдал список форматов (или только один) — один вариант «лучшее»
        size, exact = estimate_size(info) if info else (0, False)
        if _fits(size, exact, max_bytes):
            options.append(QualityOption(f"🎬 Лучшее качество{_size_label(size, exact)}",
                                         None, False, size, exact))
        else:
            hidden += 1

    # MP3: оценка по целевому битрейту, иначе по исходной дорожке
    if duration:
        mp3_size, mp3_exact = int(MP3_BITRATE_KBPS * 1000 / 8 * duration), False
    else:
        mp3_size, mp3_exact = audio_size, False
    if (formats or info) and _fits(mp3_size, mp3_exact, max_bytes):
        options.append(QualityOption(f"🎵 MP3{_size_label(mp3_size, mp3_exact)}",
                                     None, True, mp3_size, mp3_exact))
    return options, hidden

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
from bot.media import probe_url, ratio_label

MP3_BITRATE_KBPS = 192
MAX_VIDEO_OPTIONS = 6  # на каждую ориентацию
# Экстракторы, у которых «height» формата — это короткая сторона кадра
# (Twitch: portrait-720 — это 720x1280, а height=720)
SHORT_SIDE_HEIGHT_EXTRACTORS = {"TwitchClips"}
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
    # Для аудио: "mp3" / "m4a" — через FFmpegExtractAudio (без перекодирования,
    # если исходник уже в этом кодеке); "original" — файл как есть
    audio_format: str = "mp3"
    # Размеры кадра исходника (для экрана выбора соотношения сторон)
    src_width: int = 0
    src_height: int = 0
    # Короткая подпись для раскладки в 2 столбца: «📱 1080p60 · ≈23 MB»
    compact: str = ""
    # Группа для раскладки: ориентация ('h'/'v'/'s'/'') или 'a' — аудио.
    # В одной строке клавиатуры не смешиваются кнопки разных групп.
    group: str = ""

    @property
    def short(self) -> str:
        """Название для статусных сообщений: '720p', 'MP3', 'AAC', 'Оригинал'."""
        if self.audio_only:
            return {"mp3": "MP3", "m4a": "AAC", "original": "Оригинал"}.get(self.audio_format, "Audio")
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


def _size_short(size: int, exact: bool) -> str:
    """Компактный размер для узкой кнопки: 703 KB, 4.8 MB, 23 MB, 1.31 GB."""
    if not size:
        return ""
    mb = size / (1024 * 1024)
    if mb < 1:
        txt = f"{size / 1024:.0f} KB"
    elif mb < 10:
        txt = f"{mb:.1f} MB"
    elif mb < 1024:
        txt = f"{mb:.0f} MB"
    else:
        txt = f"{mb / 1024:.2f} GB"
    return f" · {'' if exact else '≈'}{txt}"


def _size_label(size: int, exact: bool) -> str:
    if not size:
        return ""
    return f" · {'' if exact else '≈'}{format_file_size(size)}"


def _fits(size: int, exact: bool, max_bytes: int) -> bool:
    if not size:
        return True  # размер неизвестен — пусть решит проверка при загрузке
    return size <= (max_bytes if exact else max_bytes * APPROX_OVERSHOOT)


def _is_mp3(f: dict) -> bool:
    return (f.get("acodec") or "").lower() == "mp3" or f.get("ext") == "mp3"


def _is_aac(f: dict) -> bool:
    return (f.get("acodec") or "").lower().startswith(("mp4a", "aac")) or f.get("ext") == "m4a"


def _abr(f: dict) -> int:
    return int(f.get("abr") or f.get("tbr") or 0)


def _kbps(f: dict) -> str:
    return f" {_abr(f)} kbps" if _abr(f) else ""


def _audio_only_options(audios: list[dict], duration: float, max_bytes: int) -> tuple[list[QualityOption], int]:
    """Источник без видео (SoundCloud и т.п.): MP3 / AAC / оригинал загрузки."""
    options: list[QualityOption] = []
    hidden = 0
    # превью Go+ (30 секунд) не предлагаем, если есть полные версии
    full = [f for f in audios if "preview" not in (f.get("format_id") or "")] or audios

    def add(label: str, f: dict | None, selector: str | None, fmt: str, fallback_kbps: int = 0):
        nonlocal hidden
        if f is not None:
            size, exact = _fsize(f, duration)
        else:
            size, exact = (int(fallback_kbps * 1000 / 8 * duration), False) if duration else (0, False)
        if not _fits(size, exact, max_bytes):
            hidden += 1
            return
        short = label.replace(" kbps", "").replace(" (M4A)", "")
        options.append(QualityOption(f"{label}{_size_label(size, exact)}", selector, True,
                                     size, exact, audio_format=fmt,
                                     compact=f"{short}{_size_short(size, exact)}", group="a"))

    original = next((f for f in full if f.get("format_id") == "download"), None)
    mp3 = max((f for f in full if _is_mp3(f)), key=_abr, default=None)
    aac = max((f for f in full if _is_aac(f)), key=_abr, default=None)

    if mp3:
        add(f"🎵 MP3{_kbps(mp3)}", mp3,
            f"{mp3['format_id']}/bestaudio[acodec=mp3]/bestaudio[ext=mp3]/bestaudio/best", "mp3")
    else:
        add(f"🎵 MP3 {MP3_BITRATE_KBPS} kbps", None, "bestaudio/best", "mp3", MP3_BITRATE_KBPS)
    if aac:
        add(f"🎧 AAC{_kbps(aac)} (M4A)", aac,
            f"{aac['format_id']}/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio", "m4a")
    if original:
        ext = (original.get("ext") or "").upper()
        add(f"💾 Оригинал{f' ({ext})' if ext else ''}", original, "download/bestaudio", "original")
    return options, hidden


def _dims(f: dict, entry: dict) -> tuple[int, int]:
    """Размеры кадра формата: width/height -> aspect_ratio -> пропорция всего видео."""
    w, h = int(f.get("width") or 0), int(f.get("height") or 0)
    if w and h:
        return w, h
    ar = f.get("aspect_ratio")
    if not ar:
        ew, eh = int(entry.get("width") or 0), int(entry.get("height") or 0)
        ar = (ew / eh) if ew and eh else entry.get("aspect_ratio")
    if h and ar:
        ar = float(ar)
        # Twitch и др.: у вертикального видео «height» = короткая сторона (1080 при 1080x1920)
        short_side = (
            entry.get("_height_is_short_side")
            or entry.get("extractor_key") in SHORT_SIDE_HEIGHT_EXTRACTORS
            or str(f.get("format_id") or "").startswith("portrait")
        )
        if short_side and ar < 1:
            return h, int(round(h / ar))
        return int(round(h * ar)), h
    if w and ar:
        return w, int(round(w / float(ar)))
    return w, h


def _has_dims(entry: dict) -> bool:
    if entry.get("width") and entry.get("height"):
        return True
    return any(f.get("width") and f.get("height") or f.get("aspect_ratio")
               for f in entry.get("formats") or [] if f.get("vcodec") != "none")


def ensure_dimensions(info: dict | None) -> None:
    """
    Если площадка не отдала размеры кадра (Twitch-клипы, часть HLS), читаем их
    ffprobe'ом по ссылке на лучший видеопоток и дописываем в entry.
    Синхронно — вызывать через asyncio.to_thread.
    """
    entry = first_entry(info)
    if not entry or _has_dims(entry):
        return
    candidates = [f for f in entry.get("formats") or [entry]
                  if f.get("url") and f.get("vcodec") != "none"]
    if not candidates:
        return
    best = max(candidates, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    w, h = probe_url(best["url"], best.get("http_headers") or entry.get("http_headers"))
    if w and h:
        entry["width"], entry["height"], entry["aspect_ratio"] = w, h, round(w / h, 4)
        fh = int(best.get("height") or 0)
        if h > w and fh and abs(fh - w) <= 2 and abs(fh - h) > 2:
            entry["_height_is_short_side"] = True


def available_ratios(options: list[QualityOption]) -> list[tuple[str, str]]:
    """[(ratio, orientation)] среди видео-вариантов, без повторов, в порядке кнопок."""
    seen, out = set(), []
    for o in options:
        if o.audio_only or not (o.src_width and o.src_height):
            continue
        item = (ratio_label(o.src_width, o.src_height), orientation(o.src_width, o.src_height))
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def source_dims(info: dict | None) -> tuple[int, int]:
    """Размеры исходника: самый большой видеоформат или поля entry."""
    entry = first_entry(info) or {}
    best = (0, 0)
    for f in entry.get("formats") or []:
        if f.get("vcodec") == "none":
            continue
        w, h = _dims(f, entry)
        if w * h > best[0] * best[1]:
            best = (w, h)
    if not best[0]:
        best = (int(entry.get("width") or 0), int(entry.get("height") or 0))
    return best


def orientation(w: int, h: int) -> str:
    """'h' — горизонтальное, 'v' — вертикальное, 's' — квадратное (±3%), '' — неизвестно."""
    if not w or not h:
        return ""
    r = w / h
    return "s" if abs(r - 1) < 0.03 else "h" if r > 1 else "v"


ORIENT_ICON = {"h": "🎬", "v": "📱", "s": "⬛", "": "🎬"}
ORIENT_NAME = {"h": "горизонтальное", "v": "вертикальное", "s": "квадратное"}
# Фильтр yt-dlp для fallback'ов, чтобы при протухших ссылках не подменить ориентацию.
# «?» — формат без известного aspect_ratio тоже подходит.
_ORIENT_FILTER = {"h": "[aspect_ratio>?1]", "v": "[aspect_ratio<?1]", "s": "", "": ""}
_ORIENT_ORDER = {"h": 0, "s": 1, "v": 2, "": 3}


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
    # acodec=None у аудио бывает в HLS без CODECS (X: hls-audio-128000-Audio)
    audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"
              and f.get("ext") != "mhtml"]
    best_audio = max(
        audios,
        # m4a/AAC — родной для MP4 и Telegram, затем битрейт
        key=lambda f: (f.get("ext") in ("m4a", "mp4"), f.get("abr") or f.get("tbr") or 0),
        default=None,
    )
    audio_size, audio_exact = _fsize(best_audio, duration) if best_audio else (0, True)

    if not videos and audios:
        return _audio_only_options(audios, duration, max_bytes)

    # Лучший формат на каждое (разрешение, ориентация). «p» — короткая сторона кадра:
    # вертикальный Shorts 1080x1920 — это 1080p, а не 1920p. Ориентация в ключе —
    # чтобы у Twitch 720p 16:9 и 720p 9:16 были отдельными кнопками.
    best_by_height: dict[tuple[int, str], dict] = {}
    for f in videos:
        fw, fh = _dims(f, entry)
        h = (min(fw, fh) if fw and fh else int(f["height"]), orientation(fw, fh))
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
    per_orient: dict[str, int] = {}
    # Блоками по ориентации (горизонтальные, затем вертикальные), внутри — по убыванию
    for key in sorted(best_by_height, key=lambda k: (_ORIENT_ORDER[k[1]], -k[0])):
        h, orient = key
        if per_orient.get(orient, 0) >= MAX_VIDEO_OPTIONS:
            continue
        f = best_by_height[key][1]
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
        fw, fh = _dims(f, entry)
        ratio = f" · {ratio_label(fw, fh)}" if fw and fh else ""
        # fallback'и по значению height из метаданных формата + та же ориентация
        fh_sel = int(f.get("height") or h)
        of = _ORIENT_FILTER[orient]
        selector = (
            f"{primary}/bv*[height={fh_sel}]{of}+ba/b[height={fh_sel}]{of}"
            f"/bv*[height<={fh_sel}]{of}+ba/b[height<={fh_sel}]{of}/b{of}/b"
        )
        options.append(QualityOption(f"{warn}{ORIENT_ICON[orient]} {name}{ratio}{_size_label(size, exact)}",
                                     selector, False, size, exact, h,
                                     src_width=fw, src_height=fh,
                                     compact=f"{warn}{ORIENT_ICON[orient]} {name}{_size_short(size, exact)}",
                                     group=orient))
        per_orient[orient] = per_orient.get(orient, 0) + 1

    if not options and not videos:
        # Сайт не отдал список форматов (или только один) — один вариант «лучшее»
        size, exact = estimate_size(info) if info else (0, False)
        if _fits(size, exact, max_bytes):
            ew, eh = int(entry.get("width") or 0), int(entry.get("height") or 0)
            ratio = f" · {ratio_label(ew, eh)}" if ew and eh else ""
            options.append(QualityOption(f"🎬 Лучшее качество{ratio}{_size_label(size, exact)}",
                                         None, False, size, exact,
                                         src_width=ew, src_height=eh,
                                         compact=f"🎬 Лучшее{_size_short(size, exact)}",
                                         group=orientation(ew, eh)))
        else:
            hidden += 1

    # MP3: оценка по целевому битрейту, иначе по исходной дорожке
    if duration:
        mp3_size, mp3_exact = int(MP3_BITRATE_KBPS * 1000 / 8 * duration), False
    else:
        mp3_size, mp3_exact = audio_size, False
    if (formats or info) and _fits(mp3_size, mp3_exact, max_bytes):
        options.append(QualityOption(f"🎵 MP3{_size_label(mp3_size, mp3_exact)}",
                                     None, True, mp3_size, mp3_exact,
                                     compact=f"🎵 MP3{_size_short(mp3_size, mp3_exact)}", group="a"))
    return options, hidden

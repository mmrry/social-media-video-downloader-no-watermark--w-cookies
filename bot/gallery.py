"""
Фото-посты и карусели: разбор сырого ответа yt-dlp (extract_info(process=False)).

Галерея — пост, в котором есть хотя бы одно фото:
  * Instagram: фото в посте/карусели (элемент без formats, но с thumbnails)
    и фото-сторис (метка _bot_photo из InstagramStoryMediaIE);
  * TikTok: фото-пост / слайд-шоу (_bot_images из TikTokMediaIE).
Видео внутри карусели идут в тот же альбом. Пост только из видео — не галерея:
для него остаётся обычный выбор качества.
"""
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bot.extractors import best_thumbnail_urls

# Порядок предпочтения форматов картинок: JPEG/PNG Telegram принимает как есть,
# WebP конвертируется, HEIC/AVIF — последний шанс (ffmpeg может не справиться)
_EXT_RANK = {"jpeg": 0, "jpg": 0, "png": 1, "webp": 2}

# Экстракторы, у которых фото-пост = элемент без форматов + картинка в thumbnails:
# Instagram (image_versions2), 9GAG (фото-мем, images.image700), Imgur (одиночная картинка)
_THUMB_PHOTO_EXTRACTORS = ("Instagram", "NineGag", "Imgur")


@dataclass
class GalleryItem:
    kind: str                      # "photo" | "video"
    urls: list[str] = field(default_factory=list)   # фото: кандидаты, лучший первым
    entry: dict | None = None      # видео: сырой элемент yt-dlp для process_ie_result
    width: int = 0
    height: int = 0


@dataclass
class Gallery:
    items: list[GalleryItem]
    total: int                     # элементов в посте (до обрезки лимитом)
    title: str = ""
    description: str = ""
    uploader: str = ""
    webpage_url: str = ""
    extractor: str = ""
    extractor_key: str = ""
    http_headers: dict = field(default_factory=dict)
    # Музыка слайд-шоу TikTok (отдаётся отдельно как MP3)
    audio: bool = False
    audio_duration: int = 0
    track: str = ""
    artist: str = ""

    @property
    def photos(self) -> int:
        return sum(1 for i in self.items if i.kind == "photo")

    @property
    def videos(self) -> int:
        return sum(1 for i in self.items if i.kind == "video")


def _url_ext(url: str) -> str:
    path = urlparse(url).path.lower()
    return path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""


def rank_image_urls(urls: list[str]) -> list[str]:
    """JPEG/PNG вперёд, остальное — в исходном порядке после них."""
    return sorted(dict.fromkeys(urls), key=lambda u: _EXT_RANK.get(_url_ext(u), 3))


def _has_audio(entry: dict) -> bool:
    return any(f.get("vcodec") == "none" and f.get("acodec") != "none" and f.get("url")
               for f in entry.get("formats") or [])


def _artist(entry: dict) -> str:
    artists = entry.get("artists")
    if isinstance(artists, list) and artists:
        return ", ".join(str(a) for a in artists if a)
    return str(entry.get("artist") or "")


def _has_video(entry: dict) -> bool:
    formats = entry.get("formats") or ([entry] if entry.get("url") else [])
    return any(f.get("vcodec") != "none" and f.get("format_id") != "photo" for f in formats)


def _entry_items(entry: dict, thumbs_are_photos: bool) -> list[GalleryItem]:
    if images := entry.get("_bot_images"):                       # TikTok: все фото поста
        return [GalleryItem("photo", rank_image_urls(i["urls"]),
                            width=i.get("width") or 0, height=i.get("height") or 0)
                for i in images if i.get("urls")]
    if photo := entry.get("_bot_photo"):                         # Instagram story: фото
        return [GalleryItem("photo", list(photo))]
    if _has_video(entry):
        return [GalleryItem("video", entry=entry,
                            width=int(entry.get("width") or 0), height=int(entry.get("height") or 0))]
    if thumbs_are_photos and (urls := best_thumbnail_urls(entry.get("thumbnails"))):
        return [GalleryItem("photo", urls)]                      # Instagram post: фото
    return []


def gallery_from_info(raw: dict | None, max_items: int) -> Gallery | None:
    """Gallery, если в посте есть фото; иначе None (обычный видео-сценарий)."""
    if not isinstance(raw, dict):
        return None
    extractor_key = raw.get("extractor_key") or ""
    # thumbnails как фото — только там, где у фото-поста картинка лежит в thumbnails
    # (элемент без видеоформатов); у остальных площадок thumbnails — обложки видео
    thumbs_are_photos = extractor_key.startswith(_THUMB_PHOTO_EXTRACTORS)

    if raw.get("_type") == "playlist":
        entries = raw.get("entries") or []
        if not isinstance(entries, list):   # ленивый список (каналы, плейлисты) — не пост
            return None
    elif raw.get("_type", "video") == "video":
        entries = [raw]
    else:
        return None

    items: list[GalleryItem] = []
    for e in entries:
        if isinstance(e, dict) and e.get("_type", "video") == "video":
            items.extend(_entry_items(e, thumbs_are_photos))
    if not any(i.kind == "photo" for i in items):
        return None

    first = next((e for e in entries if isinstance(e, dict)), {})
    # Музыка — только у слайд-шоу (TikTok): у карусели Instagram её нет в ответе
    music = next((e for e in entries if isinstance(e, dict) and e.get("_bot_images") and _has_audio(e)), None)
    return Gallery(
        items=items[:max_items],
        total=len(items),
        title=raw.get("title") or first.get("title") or "",
        description=raw.get("description") or first.get("description") or "",
        uploader=(raw.get("uploader") or raw.get("channel")
                  or first.get("uploader") or first.get("channel") or ""),
        webpage_url=raw.get("webpage_url") or "",
        extractor=raw.get("extractor") or "",
        extractor_key=extractor_key,
        http_headers=dict(raw.get("http_headers") or first.get("http_headers") or {}),
        audio=music is not None,
        audio_duration=int((music or {}).get("duration") or 0),
        track=str((music or {}).get("track") or ""),
        artist=_artist(music or {}),
    )


def has_media(raw: dict | None) -> bool:
    """Есть ли в сыром ответе хоть что-то скачиваемое (для поста без фото)."""
    if not isinstance(raw, dict):
        return False
    if raw.get("_type") == "playlist":
        entries = raw.get("entries")
        if not isinstance(entries, list):
            return True  # ленивый список — решит обработка
        return any(isinstance(e, dict) and (e.get("_type", "video") != "video" or has_media(e))
                   for e in entries)
    if raw.get("_type", "video") != "video":
        return True
    return bool(raw.get("formats") or raw.get("url"))

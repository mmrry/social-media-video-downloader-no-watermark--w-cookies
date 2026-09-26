"""
yt-dlp экстрактор для клипов («моментов») VK Video Live.

yt-dlp из коробки умеет только стримы (live.vkvideo.ru/<channel>) и записи
(live.vkvideo.ru/<channel>/record/<uuid>) — см. yt_dlp/extractor/vk.py (VKPlayIE).
Клипы вида live.vkvideo.ru/<channel>/clip/<uuid> он не распознаёт.

Публичного API для клипов нет (все варианты api.live.vkvideo.ru / api.vkplay.live
дают 404), но страница клипа рендерится на сервере и содержит JSON-состояние
с playerUrls (HLS на okcdn.ru). Поэтому:
  1. Ищем JSON с "playerUrls" в любом <script> страницы (id тега не важен;
     поддерживаются `window.X = {...}`, application/json и JSON.parse("...")).
  2. Последний шанс — прямые ссылки на .m3u8/.mp4 в HTML.

Структура ответа ищется рекурсивно (любой dict с playerUrls), поэтому
изменения вложенности в API не ломают экстрактор.
"""
import json
import re

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    ExtractorError,
    int_or_none,
    parse_resolution,
    str_or_none,
    traverse_obj,
    url_or_none,
)

_HOSTS_RE = r"(?:vkplay\.live|live\.vk(?:play|video)\.ru)"

_RESOLUTIONS = {
    "tiny": "256x144",
    "lowest": "426x240",
    "low": "640x360",
    "medium": "852x480",
    "high": "1280x720",
    "full_hd": "1920x1080",
    "quad_hd": "2560x1440",
    "ultra_hd": "3840x2160",
}

# Живые DASH-потоки yt-dlp не поддерживает (как и в VKPlayBaseIE)
_SKIP_TYPES = {"live_dash", "live_playback_dash"}


def _walk(obj, chain=()):
    """Все dict с непустым списком playerUrls + цепочка родителей (для метаданных)."""
    if isinstance(obj, dict):
        if isinstance(obj.get("playerUrls"), list) and obj["playerUrls"]:
            yield obj, chain
        for v in obj.values():
            yield from _walk(v, chain + (obj,))
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, chain)


_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
_JSON_PARSE_RE = re.compile(r'JSON\.parse\(\s*"((?:[^"\\]|\\.)*)"\s*\)', re.S)
_MAX_DECODE_TRIES = 200


def _json_from_scripts(webpage: str):
    """Все JSON-объекты из <script>, внутри которых есть playerUrls."""
    decoder = json.JSONDecoder()
    for body in _SCRIPT_RE.findall(webpage or ""):
        if "playerUrls" not in body:
            continue
        # window.__STATE__ = JSON.parse("{\"a\":1}")
        for m in _JSON_PARSE_RE.finditer(body):
            try:
                yield json.loads(json.loads(f'"{m.group(1)}"'))
            except ValueError:
                pass
        # <script type="application/json">{...}</script> / window.X = {...};
        pos, tries = body.find("{"), 0
        while pos != -1 and tries < _MAX_DECODE_TRIES:
            tries += 1
            try:
                obj, end = decoder.raw_decode(body, pos)
            except ValueError:
                pos = body.find("{", pos + 1)
                continue
            if isinstance(obj, (dict, list)) and "playerUrls" in body[pos:pos + end - pos]:
                yield obj
            pos = body.find("{", end)


class VKVideoLiveClipIE(InfoExtractor):
    IE_NAME = "vkvideolive:clip"
    IE_DESC = "VK Video Live clips (моменты)"
    _VALID_URL = (
        rf"https?://(?:www\.)?{_HOSTS_RE}/(?P<channel>[^/?#]+)/"
        r"(?:clips?|moments?)/(?P<id>[\w-]+)"
    )

    # ─── helpers ───

    @staticmethod
    def _pick(root, clip_id: str):
        found = list(_walk(root))
        if not found:
            return None
        # На странице могут быть и соседние клипы (рекомендации) —
        # предпочитаем узел, в цепочке которого встречается id нашего клипа
        def mentions(d: dict) -> bool:
            return any(isinstance(v, (str, int)) and str(v) == clip_id for v in d.values())

        for node, chain in found:
            if any(mentions(d) for d in (node, *chain)):
                return node, chain
        return found[0]

    @staticmethod
    def _meta(node: dict, chain: tuple) -> dict:
        scopes = (node, *reversed(chain))  # от самого вложенного к корню

        def first(*paths):
            for d in scopes:
                for path in paths:
                    v = traverse_obj(d, path)
                    if v not in (None, "", [], {}):
                        return v
            return None

        category = first(("category", "title"))
        return {
            "title": str_or_none(first("title", "name")),
            "thumbnail": url_or_none(first("previewUrl", "preview", "thumbnailUrl", "coverUrl")),
            "duration": int_or_none(first("duration")),
            "timestamp": int_or_none(first("createdAt", "startTime")),
            "uploader": str_or_none(first(
                ("blog", "owner", "nick"), ("blog", "owner", "displayName"),
                ("user", "nick"), ("owner", "nick"), ("author", "nick"),
            )),
            "uploader_id": str_or_none(first(
                ("blog", "owner", "id"), ("user", "id"), ("owner", "id"),
            )),
            "view_count": int_or_none(first(("count", "views"), "views", "viewsCount")),
            "like_count": int_or_none(first(("count", "likes"), "likes")),
            "categories": [category] if isinstance(category, str) and category else None,
        }

    def _formats_from_player_urls(self, player_urls: list, video_id: str) -> list:
        formats, seen = [], set()
        for p in player_urls:
            url = url_or_none((p or {}).get("url"))
            ftype = str_or_none((p or {}).get("type")) or ""
            if not url or url in seen or ftype in _SKIP_TYPES:
                continue
            seen.add(url)
            if ftype.endswith("hls") or ".m3u8" in url:
                formats.extend(self._extract_m3u8_formats(
                    url, video_id, "mp4", m3u8_id=ftype or "hls", fatal=False))
            elif ftype.endswith("dash") or ".mpd" in url:
                formats.extend(self._extract_mpd_formats(
                    url, video_id, mpd_id=ftype or "dash", fatal=False))
            else:
                formats.append({
                    "url": url,
                    "ext": "mp4",
                    "format_id": ftype or None,
                    **parse_resolution(_RESOLUTIONS.get(ftype)),
                })
        return formats

    def _formats_from_html(self, webpage: str, video_id: str) -> list:
        formats, seen = [], set()
        pattern = r'https?:(?:\\?/){2}(?:[^"\'\s<>\\]|\\/|\\u0026)+?\.(?:m3u8|mp4)(?:[^"\'\s<>\\]|\\/|\\u0026)*'
        for raw in re.findall(pattern, webpage):
            url = raw.replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&")
            if url in seen:
                continue
            seen.add(url)
            if ".m3u8" in url:
                formats.extend(self._extract_m3u8_formats(url, video_id, "mp4", fatal=False))
            else:
                formats.append({"url": url, "ext": "mp4"})
        return formats

    # ─── main ───

    def _real_extract(self, url):
        channel, clip_id = self._match_valid_url(url).group("channel", "id")
        page_url = f"https://live.vkvideo.ru/{channel}/clip/{clip_id}"
        headers = {"Referer": page_url, "Origin": "https://live.vkvideo.ru"}

        webpage = self._download_webpage(page_url, clip_id, headers=headers)

        picked = None
        for state in _json_from_scripts(webpage):
            picked = self._pick(state, clip_id)
            if picked:
                self.write_debug("clip data from page state JSON")
                break

        meta: dict = {}
        formats: list = []
        if picked:
            node, chain = picked
            formats = self._formats_from_player_urls(node["playerUrls"], clip_id)
            meta = self._meta(node, chain)
        if not formats:
            self.write_debug("page state not parsed, falling back to raw m3u8/mp4 links")
            formats = self._formats_from_html(webpage, clip_id)

        if not formats:
            raise ExtractorError(
                "Клип не найден, удалён или недоступен без авторизации", expected=True)

        # Недостающие метаданные — из OpenGraph
        meta.setdefault("title", None)
        meta["title"] = meta["title"] or self._og_search_title(webpage, default=None) \
            or self._html_extract_title(webpage, default=None)
        meta["thumbnail"] = meta.get("thumbnail") or self._og_search_thumbnail(webpage, default=None)

        return {
            **{k: v for k, v in meta.items() if v is not None},
            "id": clip_id,
            "title": meta.get("title") or f"{channel} clip",
            "uploader": meta.get("uploader") or channel,
            "webpage_url": page_url,
            "formats": formats,
            "http_headers": {"Referer": "https://live.vkvideo.ru/"},
        }


def is_vk_live_clip(url: str) -> bool:
    return bool(VKVideoLiveClipIE.suitable(url))

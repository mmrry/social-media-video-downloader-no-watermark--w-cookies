"""
Точечные патчи yt-dlp, применяются один раз при импорте downloader.

VK_API_HOST: VK-экстракторы yt-dlp жёстко ходят на https://vk.com/al_video.php
(и video_ext.php) — даже для ссылок vk.ru / vkvideo.ru. Если vk.com с сервера
недоступен (таймаут, блокировка маршрута), а зеркало vk.ru / vkvideo.ru
доступно — переписываем хост во всех запросах VK-экстракторов.
"""
import logging
import re

logger = logging.getLogger(__name__)

_VK_COM_RE = re.compile(r"^https?://(?:(?:www|m|new)\.)?vk\.com/", re.I)


def apply_vk_host_patch(host: str) -> None:
    host = (host or "").strip().lower()
    if not host or host == "vk.com":
        return

    from yt_dlp.extractor import vk
    from yt_dlp.networking import Request

    original = vk.VKBaseIE._download_webpage_handle
    if getattr(original, "_bot_patched", False):
        return

    def rewrite(url: str) -> str:
        return _VK_COM_RE.sub(f"https://{host}/", url)

    def patched(self, url_or_request, video_id, *args, **kwargs):
        if isinstance(url_or_request, str):
            url_or_request = rewrite(url_or_request)
        elif isinstance(url_or_request, Request):
            url_or_request.url = rewrite(url_or_request.url)
        headers = kwargs.get("headers")
        if headers and isinstance(headers.get("Referer"), str):
            kwargs["headers"] = {**headers, "Referer": rewrite(headers["Referer"])}
        return original(self, url_or_request, video_id, *args, **kwargs)

    patched._bot_patched = True
    vk.VKBaseIE._download_webpage_handle = patched
    logger.info("yt-dlp VK API host: vk.com -> %s", host)

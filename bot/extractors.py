"""
Подклассы штатных экстракторов yt-dlp, которые сохраняют фото.

yt-dlp ориентирован на видео и картинки из постов отбрасывает:

* TikTok — фото-посты (слайд-шоу, ``/@user/photo/<id>``): ``TikTokIE`` не принимает
  ссылки ``/photo/``, а по ссылке ``/video/<id>`` отдаёт только музыку.
  Данные поста при этом уже скачаны — ``itemStruct.imagePost.images`` (веб) или
  ``image_post_info.images`` (API приложения). Переопределяем парсеры и кладём
  URL картинок в ``_bot_images`` — без дополнительных запросов.
  Страница отдаёт ``webapp.video-detail`` только по ``/video/``-ссылке, поэтому
  ``/photo/`` принимаем, а запрос идёт штатно через ``_create_url`` (``/video/``).

* Instagram stories — ``InstagramStoryIE`` пропускает элементы без видеоформатов,
  то есть все фото-сторис. Для фото добавляем «формат»-картинку и метку ``_bot_photo``,
  чтобы элемент не отфильтровался.

Посты Instagram (``/p/``) подкласса не требуют: штатный экстрактор отдаёт фото
элементами без ``formats``, но с ``thumbnails`` (это и есть ``image_versions2``).

* Дзен, ролики (``dzen.ru/shorts/<id>``) — ``ZenYandexIE`` принимает только
  ``/video/watch/`` и ``/media/``, а ``/shorts/`` уходит в экстрактор канала (канал «shorts»).
  Подкласс разбирает страницу ролика тем же кодом (JSON ``ssrData``); если структура
  страницы другая — переходит на ``dzen.ru/video/watch/<id>`` (тот же id публикации).

* Vimeo без входа — для ``vimeo.com/<id>`` штатный экстрактор идёт в API клиентом
  ``web``, который теперь требует вход («The web client only works when logged-in»).
  Страницу плеера ``player.vimeo.com/video/<id>`` тот же экстрактор разбирает без API
  (``playerConfig``) — туда и перенаправляем, если cookies Vimeo нет. С cookies
  (``VIMEO_COOKIES_FILE``) — штатный путь через API (плюс исходный файл, если автор разрешил).

Видео все подклассы обрабатывают ровно как штатные экстракторы.
"""
from yt_dlp.extractor.instagram import InstagramStoryIE
from yt_dlp.extractor.tiktok import TikTokIE
from yt_dlp.extractor.vimeo import VimeoIE
from yt_dlp.extractor.yandexvideo import ZenYandexIE
from yt_dlp.utils import int_or_none, traverse_obj, url_or_none


def best_thumbnail_urls(thumbnails: list | None, limit: int = 2) -> list[str]:
    """URL самых больших картинок (по площади), лучший первым."""
    items = [t for t in thumbnails or [] if isinstance(t, dict) and url_or_none(t.get("url"))]
    items.sort(key=lambda t: (int_or_none(t.get("width")) or 0) * (int_or_none(t.get("height")) or 0),
               reverse=True)
    return [t["url"] for t in items[:limit]]


def _images(raw: list, url_path: tuple, w_key: str, h_key: str) -> list[dict]:
    out = []
    for img in raw:
        urls = [u for u in traverse_obj(img, (*url_path, ..., {url_or_none})) or [] if u]
        if urls:
            out.append({
                "urls": list(dict.fromkeys(urls)),
                "width": int_or_none(traverse_obj(img, w_key)) or 0,
                "height": int_or_none(traverse_obj(img, h_key)) or 0,
            })
    return out


class TikTokMediaIE(TikTokIE):
    IE_NAME = "tiktok:media"
    _VALID_URL = (r"https?://(?:www\.|m\.)?tiktokv?\.com/"
                  r"(?:embed|(?:share|@(?P<user_id>[\w.-]+)?)/(?:video|photo))/(?P<id>\d+)")
    _TESTS = []

    def _parse_aweme_video_web(self, aweme_detail, webpage_url, video_id, extract_flat=False):
        info = super()._parse_aweme_video_web(aweme_detail, webpage_url, video_id, extract_flat)
        images = _images(traverse_obj(aweme_detail, ("imagePost", "images", ..., {dict})) or [],
                         ("imageURL", "urlList"), "imageWidth", "imageHeight")
        if images:
            info["_bot_images"] = images
        return info

    def _parse_aweme_video_app(self, aweme_detail):
        info = super()._parse_aweme_video_app(aweme_detail)
        raw = traverse_obj(aweme_detail, ("image_post_info", "images", ..., "display_image", {dict})) or []
        images = _images(raw, ("url_list",), "width", "height")
        if images:
            info["_bot_images"] = images
        return info


class InstagramStoryMediaIE(InstagramStoryIE):
    IE_NAME = "instagram:story:media"
    _TESTS = []

    def _extract_product(self, product_info, video_id=None, get_comments=True):
        info = super()._extract_product(product_info, video_id, get_comments)
        if info.get("_type") != "playlist" and not info.get("formats"):
            urls = best_thumbnail_urls(info.get("thumbnails"))
            if urls:
                info["_bot_photo"] = urls
                # Без «формата» InstagramStoryIE выбрасывает элемент из списка сторис
                info["formats"] = [{"url": urls[0], "format_id": "photo", "ext": "jpg",
                                    "vcodec": "none", "acodec": "none"}]
        return info


class ZenYandexShortsIE(ZenYandexIE):
    IE_NAME = "dzen.ru:shorts"
    _VALID_URL = r"https?://(?:zen\.yandex|dzen)\.ru/shorts/(?P<id>[a-z0-9-]+)"
    _TESTS = []

    def _real_extract(self, url):
        video_id = self._match_id(url)
        try:
            return super()._real_extract(url)
        except Exception as e:  # noqa: BLE001 — другая структура страницы ролика
            self.write_debug(f"dzen shorts page: {e}; trying video/watch")
            return self.url_result(f"https://dzen.ru/video/watch/{video_id}", ZenYandexIE, video_id)


class VimeoNoLoginIE(VimeoIE):
    IE_NAME = "vimeo:nologin"
    _TESTS = []

    def _extract_from_api(self, video_id, unlisted_hash=None):
        client = self._get_requested_client()
        if self._is_logged_in or not self._CLIENT_CONFIGS[client]["REQUIRES_AUTH"]:
            return super()._extract_from_api(video_id, unlisted_hash)
        player_url = f"https://player.vimeo.com/video/{video_id}"
        if unlisted_hash:
            player_url += f"?h={unlisted_hash}"   # приватное видео по ссылке
        self.write_debug(f"{client} API client needs login; using player page {player_url}")
        return self.url_result(player_url, VimeoIE, video_id)

"""Фото-посты, альбомы и ошибки. Сеть площадок подменена фикстурами, разбор — штатный yt-dlp.

  pip install pytest && BOT_TOKEN=x DOWNLOAD_DIR=/tmp/dl python -m pytest -q tests
"""
import asyncio
import functools
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from bot import errors
from bot.config import DOWNLOAD_DIR
from bot.extractors import TikTokMediaIE, InstagramStoryMediaIE
from bot.gallery import gallery_from_info, Gallery, GalleryItem, rank_image_urls

ASSETS = Path(__file__).parent / "assets"


# ───────────── errors ─────────────

USER_EXAMPLE = ("ERROR: [instagram:story] 4002848070710841711: This content is unreachable. "
                "Use --cookies-from-browser or --cookies for the authentication. See  "
                "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
                "for how to manually pass cookies")


@pytest.mark.parametrize("raw,expected", [
    (USER_EXAMPLE, errors.AUTH),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", errors.AUTH),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot", errors.BOT_CHECK),
    ("ERROR: [youtube] abc: Sign in to confirm your age", errors.AGE),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", errors.GEO),
    ("ERROR: [youtube] abc: This video is geo-restricted", errors.GEO),
    ("ERROR: [TikTok] 7: Your IP address is blocked from accessing this post", errors.BLOCKED),
    ("ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found", errors.GONE),
    ("ERROR: [Instagram] C1: HTTP Error 429: Too Many Requests", errors.RATE_LIMIT),
    ("ERROR: Unsupported URL: https://example.com/x", errors.UNSUPPORTED),
    ("ERROR: [vk] 1_2: Connection to vk.com timed out", errors.NETWORK),
    ("ERROR: [Instagram] C1: There is no video in this post", errors.NO_MEDIA),
    ("ERROR: [youtube] abc: Video unavailable", errors.GONE),
    # id содержит 403/404/429 — не должно влиять
    ("ERROR: [instagram] 4034044290: something odd happened", errors.DEFAULT),
    ("totally unknown", errors.DEFAULT),
])
def test_describe(raw, expected):
    msg = errors.describe(raw)
    assert msg == expected
    assert "yt-dlp" not in msg and "--cookies" not in msg and "http" not in msg


# ───────────── URL routing ─────────────

@pytest.mark.parametrize("url,ok", [
    ("https://www.tiktok.com/@user.name/photo/7412345678901234567", True),
    ("https://tiktok.com/@u/photo/7412345678901234567", True),
    ("https://www.tiktok.com/@u/video/7412345678901234567", True),
    ("https://www.tiktok.com/@u", False),
    ("https://vm.tiktok.com/ZMabc/", False),
])
def test_tiktok_suitable(url, ok):
    assert bool(TikTokMediaIE.suitable(url)) is ok


def test_story_suitable():
    assert InstagramStoryMediaIE.suitable("https://www.instagram.com/stories/user/4002848070710841711/")
    assert not InstagramStoryMediaIE.suitable("https://www.instagram.com/p/ABC/")


# ───────────── fixtures ─────────────

def ig_photo(pk, w=1080, h=1350):
    return {"pk": pk, "media_type": 1, "image_versions2": {"candidates": [
        {"url": f"https://scontent.cdninstagram.com/{pk}_big.jpg", "width": w, "height": h},
        {"url": f"https://scontent.cdninstagram.com/{pk}_small.jpg", "width": 320, "height": 400},
    ]}}


def ig_video(pk, url="https://scontent.cdninstagram.com/v.mp4"):
    return {**ig_photo(pk), "media_type": 2, "video_duration": 3.0, "has_audio": True,
            "video_versions": [{"url": url, "width": 720, "height": 1280, "type": 101}]}


IG_USER = {"username": "nasa", "full_name": "NASA", "pk": 1}


def ig_post(*items, caption="Hello #space"):
    post = {"pk": 3001, "code": "C1", "user": IG_USER, "caption": {"text": caption}, "taken_at": 1}
    if len(items) == 1:
        return {**post, **items[0], "pk": 3001}
    return {**post, "carousel_media": list(items)}


TT_IMAGES = [{"imageURL": {"urlList": [
    f"https://p16-sign.tiktokcdn.com/obj/img{i}~tplv-photomode-image.webp?x=1",
    f"https://p16-sign.tiktokcdn.com/obj/img{i}~tplv-photomode-image.jpeg?x=1"]},
    "imageWidth": 1080, "imageHeight": 1440} for i in range(12)]


def tt_item(images=True):
    item = {"id": "7412345678901234567", "desc": "my trip", "createTime": 1,
            "author": {"uniqueId": "traveller", "nickname": "Trav", "id": "9", "secUid": "S"},
            "music": {"title": "song", "playUrl": "https://sf16.tiktokcdn.com/music.mp3", "duration": 20},
            "video": {"duration": 0}, "stats": {}}
    if images:
        item["imagePost"] = {"images": TT_IMAGES}
    else:
        item["video"] = {"duration": 10, "width": 720, "height": 1280,
                         "playAddr": "https://v16-webapp.tiktok.com/video.mp4", "bitrate": 1000000}
    return item


@pytest.fixture
def fake_net(monkeypatch):
    """Подмена только сетевых методов экстракторов — разбор ответов остаётся штатным."""
    from yt_dlp.extractor import instagram, tiktok
    state = {"ig": None, "tt": None, "story": None}

    def ig_real_extract(self, url):
        return self._extract_product(state["ig"], get_comments=False)
    monkeypatch.setattr(instagram.InstagramIE, "_real_initialize", lambda self: None)
    monkeypatch.setattr(instagram.InstagramIE, "_real_extract", ig_real_extract)
    monkeypatch.setattr(tiktok.TikTokBaseIE, "_extract_web_data_and_status",
                        lambda self, url, vid, fatal=True: (state["tt"], 0))
    monkeypatch.setattr(tiktok.TikTokVMIE, "_real_extract",
                        lambda self, url: self.url_result(
                            "https://www.tiktok.com/@traveller/photo/7412345678901234567"))

    def story_download_webpage(self, url, *a, **k):
        return '{"user":{"pk":"1","username":"nasa"}}'

    def story_download_json(self, url, *a, **k):
        return {"reels": {"1": {"user": {"pk": "1", "full_name": "NASA"}, "items": state["story"]}}}
    monkeypatch.setattr(InstagramStoryMediaIE, "_download_webpage", story_download_webpage)
    monkeypatch.setattr(InstagramStoryMediaIE, "_download_json", story_download_json)
    return state


# ───────────── probe_media ─────────────

def test_ig_carousel_mixed(fake_net):
    from bot.downloader import probe_media
    fake_net["ig"] = ig_post(ig_photo(1), ig_video(2), ig_photo(3))
    g = probe_media("https://www.instagram.com/p/C1/")
    assert isinstance(g, Gallery)
    assert [i.kind for i in g.items] == ["photo", "video", "photo"]
    assert g.items[0].urls[0].endswith("1_big.jpg")
    assert g.description == "Hello #space" and g.uploader == "NASA"


def test_ig_single_photo(fake_net):
    from bot.downloader import probe_media
    fake_net["ig"] = ig_post(ig_photo(1))
    g = probe_media("https://www.instagram.com/p/C1/")
    assert isinstance(g, Gallery) and len(g.items) == 1 and g.items[0].kind == "photo"


def test_ig_single_video_keeps_video_flow(fake_net):
    from bot.downloader import probe_media
    from bot.formats import build_options
    fake_net["ig"] = ig_post(ig_video(1))
    info = probe_media("https://www.instagram.com/reel/C1/")
    assert isinstance(info, dict) and info.get("formats")
    options, _ = build_options(info, 50 * 1024 * 1024)
    assert any(not o.audio_only for o in options)


def test_ig_carousel_only_videos_keeps_video_flow(fake_net):
    from bot.downloader import probe_media, first_entry
    fake_net["ig"] = ig_post(ig_video(1), ig_video(2))
    info = probe_media("https://www.instagram.com/p/C1/")
    assert isinstance(info, dict) and first_entry(info).get("formats")


def test_tiktok_photo(fake_net):
    from bot.downloader import probe_media
    fake_net["tt"] = tt_item()
    g = probe_media("https://www.tiktok.com/@traveller/photo/7412345678901234567")
    assert isinstance(g, Gallery) and g.photos == 12 and g.videos == 0
    assert ".jpeg" in g.items[0].urls[0]          # JPEG раньше WebP
    assert g.uploader in ("traveller", "Trav") and g.description == "my trip"


def test_tiktok_short_link_resolves(fake_net):
    from bot.downloader import probe_media
    fake_net["tt"] = tt_item()
    assert isinstance(probe_media("https://vm.tiktok.com/ZMabc"), Gallery)


def test_tiktok_video_keeps_video_flow(fake_net):
    from bot.downloader import probe_media
    fake_net["tt"] = tt_item(images=False)
    info = probe_media("https://www.tiktok.com/@traveller/video/7412345678901234567")
    assert isinstance(info, dict)


def test_story_photo_and_video(fake_net):
    from bot.downloader import probe_media
    fake_net["story"] = [{**ig_photo(4002848070710841711), "user": IG_USER}]
    g = probe_media("https://www.instagram.com/stories/nasa/4002848070710841711/")
    assert isinstance(g, Gallery) and g.items[0].kind == "photo"
    fake_net["story"] = [{**ig_video(4002848070710841711), "user": IG_USER}]
    info = probe_media("https://www.instagram.com/stories/nasa/4002848070710841711/")
    assert isinstance(info, dict) and info.get("formats")


def test_story_unreachable_is_friendly(fake_net, monkeypatch):
    from bot.downloader import probe_media, DownloadError
    monkeypatch.setattr(InstagramStoryMediaIE, "_download_webpage", lambda *a, **k: "<html></html>")
    with pytest.raises(DownloadError) as ei:
        probe_media("https://www.instagram.com/stories/nasa/4002848070710841711/")
    assert str(ei.value) == errors.AUTH


def test_cap_items():
    raw = {"_type": "video", "id": "1", "extractor_key": "TikTokMedia",
           "_bot_images": [{"urls": ["https://x/a.jpeg"]}] * 40}
    g = gallery_from_info(raw, 35)
    assert len(g.items) == 35 and g.total == 40


def test_rank_urls():
    assert rank_image_urls(["https://a/x.heic", "https://a/x.webp", "https://a/x.jpeg?y=1"])[0].endswith("jpeg?y=1")


def test_thumbnail_is_not_photo_outside_instagram():
    raw = {"_type": "video", "id": "1", "extractor_key": "TikTokMedia", "formats": [],
           "thumbnails": [{"url": "https://x/cover.jpg"}]}
    assert gallery_from_info(raw, 10) is None


# ───────────── download + prepare (локальный HTTP) ─────────────

@pytest.fixture(scope="module")
def http_server():
    ASSETS.mkdir(exist_ok=True)
    ff = ["ffmpeg", "-y", "-v", "error"]
    subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc=s=1080x1350", "-frames:v", "1",
                         str(ASSETS / "a.webp")], check=True)
    subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc=s=800x600", "-frames:v", "1",
                         str(ASSETS / "b.jpg")], check=True)
    subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc=s=6000x4500", "-frames:v", "1",
                         str(ASSETS / "huge.png")], check=True)
    subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc=s=720x1280:d=2", "-f", "lavfi", "-i",
                         "sine=d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                         "-shortest", str(ASSETS / "v.mp4")], check=True)
    subprocess.run(ff + ["-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", str(ASSETS / "music.m4a")],
                   check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ASSETS))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_download_gallery_and_prepare(http_server):
    from bot.downloader import download_gallery, cleanup_by_id
    from bot.media import prepare_photo, image_kind, probe_image, prepare_video
    g = Gallery(items=[
        GalleryItem("photo", [f"{http_server}/missing.jpeg", f"{http_server}/a.webp"]),
        GalleryItem("photo", [f"{http_server}/b.jpg"]),
        GalleryItem("photo", [f"{http_server}/huge.png"]),
        GalleryItem("video", entry={"id": "v1", "title": "v", "formats": [
            {"url": f"{http_server}/v.mp4", "format_id": "101", "width": 720, "height": 1280}]}),
        GalleryItem("photo", [f"{http_server}/nope.jpg"]),
    ], total=5, extractor="instagram", extractor_key="Instagram",
        webpage_url="https://www.instagram.com/p/C1/")
    dl = download_gallery("https://www.instagram.com/p/C1/", g)
    try:
        assert [f.kind for f in dl.files] == ["photo", "photo", "photo", "video"]
        assert dl.failed == 1
        webp, jpg, png, video = dl.files
        p1 = prepare_photo(webp.path)
        assert image_kind(p1) == "jpeg" and probe_image(p1) == (1080, 1350)
        p2 = prepare_photo(jpg.path)
        assert p2 == jpg.path                                    # JPEG как есть
        p3 = prepare_photo(png.path)
        assert image_kind(p3) == "jpeg" and max(probe_image(p3)) == 2560   # 6000+4500 > 10000
        pv = prepare_video(str(video.path))
        assert pv.meta.width == 720 and pv.meta.height == 1280 and pv.thumbnail
    finally:
        cleanup_by_id(dl.file_id)
    assert not list(DOWNLOAD_DIR.glob(f"{dl.file_id}.*"))


def test_download_gallery_all_failed(http_server):
    from bot.downloader import download_gallery, DownloadError
    g = Gallery(items=[GalleryItem("photo", [f"{http_server}/none.jpg"])], total=1)
    with pytest.raises(DownloadError) as ei:
        download_gallery("https://www.instagram.com/p/C1/", g)
    assert str(ei.value) == errors.GALLERY_FAILED


# ───────────── отправка в Telegram ─────────────

class FakeChat:
    def __init__(self, fail_media_group=0, retry_after=0):
        self.calls = []
        self.fail = fail_media_group
        self.retry_after = retry_after

    async def send_media_group(self, media, **kw):
        from telegram.error import BadRequest, RetryAfter
        if self.retry_after:
            self.retry_after -= 1
            raise RetryAfter(0)
        kinds = {type(m).__name__ for m in media}
        if self.fail and "InputMediaDocument" not in kinds:
            self.fail -= 1
            raise BadRequest("PHOTO_INVALID_DIMENSIONS")
        self.calls.append(("group", [type(m).__name__ for m in media], media[0].caption,
                           [m.caption for m in media[1:]]))

    async def send_photo(self, photo, **kw):
        self.calls.append(("photo", kw.get("caption")))

    async def send_video(self, video, **kw):
        self.calls.append(("video", kw.get("caption"), kw.get("width")))

    async def send_document(self, document, **kw):
        self.calls.append(("document", kw.get("caption")))


@pytest.fixture
def media_files(tmp_path):
    src = tmp_path / "p.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x64",
                    "-frames:v", "1", str(src)], check=True)
    return src


def _media(src, n):
    from bot.handlers import _GalleryMedia
    return [_GalleryMedia("photo", src) for _ in range(n)]


@pytest.mark.parametrize("n,expected", [(1, ["photo"]), (2, ["group"]), (10, ["group"]),
                                        (11, ["group", "photo"]), (23, ["group", "group", "group"])])
def test_send_gallery_chunks(media_files, n, expected, monkeypatch):
    from bot import handlers
    monkeypatch.setattr(handlers.asyncio, "sleep", _nosleep)
    chat = FakeChat()
    asyncio.run(handlers._send_gallery(chat, _media(media_files, n), "CAP", 5))
    assert [c[0] for c in chat.calls] == expected
    assert chat.calls[0][-1] == "CAP" or chat.calls[0][2] == "CAP" or chat.calls[0][1] == "CAP"
    for c in chat.calls[1:]:
        assert "CAP" not in c                                    # подпись только у первого альбома
    if expected[0] == "group":
        assert all(x is None for x in chat.calls[0][3])          # и только у первого элемента


_real_sleep = asyncio.sleep


async def _nosleep(*_):
    await _real_sleep(0)  # отдать управление циклу, но не ждать


def test_send_gallery_fallback_documents(media_files, monkeypatch):
    from bot import handlers
    monkeypatch.setattr(handlers.asyncio, "sleep", _nosleep)
    chat = FakeChat(fail_media_group=1)
    asyncio.run(handlers._send_gallery(chat, _media(media_files, 3), "CAP", 5))
    assert chat.calls == [("group", ["InputMediaDocument"] * 3, "CAP", [None, None])]


def test_send_gallery_retry_after(media_files, monkeypatch):
    from bot import handlers
    monkeypatch.setattr(handlers.asyncio, "sleep", _nosleep)
    chat = FakeChat(retry_after=1)
    asyncio.run(handlers._send_gallery(chat, _media(media_files, 2), "CAP", 5))
    assert [c[0] for c in chat.calls] == ["group"]


def test_caption():
    from bot.handlers import _gallery_caption, _GalleryMedia
    g = Gallery(items=[GalleryItem("photo")] * 35, total=40, description="<b>x</b> & y", uploader="NASA")
    media = [_GalleryMedia("photo", Path("a"))] * 33 + [_GalleryMedia("video", Path("b"))]
    cap = _gallery_caption(g, "Instagram", media, failed=1)
    assert "&lt;b&gt;x&lt;/b&gt;" in cap and "🖼 33 фото · 🎬 1 видео" in cap
    assert "первые 35 из 40" in cap and "Не удалось загрузить: 1" in cap


# ───────────── сквозной сценарий сообщения ─────────────

class FakeStatus:
    def __init__(self):
        self.texts, self.deleted = [], False

    async def edit_text(self, text, **kw):
        self.texts.append(text)

    async def delete(self):
        self.deleted = True


class FakeMessage:
    def __init__(self, chat):
        self.chat, self.message_id = chat, 42


def test_process_gallery_end_to_end(http_server, monkeypatch):
    from bot import handlers, downloader
    monkeypatch.setattr(handlers.asyncio, "sleep", _nosleep)

    g = Gallery(items=[GalleryItem("photo", [f"{http_server}/a.webp"]),
                       GalleryItem("photo", [f"{http_server}/b.jpg"])],
                total=2, description="post", uploader="NASA", extractor_key="Instagram")
    chat, status = FakeChat(), FakeStatus()

    async def run():
        async def no_action(*a, **k):
            return None
        chat.send_action = no_action
        await handlers._process_gallery(FakeMessage(chat), status, "https://www.instagram.com/p/C1/",
                                        "Instagram", g, user_id=1)
    asyncio.run(run())
    assert chat.calls and chat.calls[0][0] == "group" and "post" in chat.calls[0][2]
    assert status.deleted
    assert not [p for p in DOWNLOAD_DIR.iterdir() if p.name[:12].isalnum() and "." in p.name]


def test_process_gallery_error_is_friendly(monkeypatch):
    from bot import handlers
    g = Gallery(items=[GalleryItem("photo", ["http://127.0.0.1:9/none.jpg"])], total=1)
    chat, status = FakeChat(), FakeStatus()

    async def run():
        async def no_action(*a, **k):
            return None
        chat.send_action = no_action
        await handlers._process_gallery(FakeMessage(chat), status, "https://www.instagram.com/p/C1/",
                                        "Instagram", g, user_id=2)
    asyncio.run(run())
    assert errors.GALLERY_FAILED in status.texts[-1] and not chat.calls


def test_healthcheck_command(tmp_path):
    import re
    cmd = re.search(r'CMD python3 -c "(.+)"', (Path(__file__).resolve().parents[1] / "Dockerfile.python").read_text()).group(1)
    hb = Path("/tmp/bot.heartbeat")
    hb.touch()
    assert subprocess.run(["python3", "-c", cmd]).returncode == 0
    os.utime(hb, (0, 0))
    assert subprocess.run(["python3", "-c", cmd]).returncode == 1
    hb.unlink()


# ───────────── музыка слайд-шоу ─────────────

def test_slideshow_audio_metadata(fake_net):
    from bot.downloader import probe_media
    fake_net["tt"] = tt_item()
    g = probe_media("https://vm.tiktok.com/ZMabc")
    assert g.audio and g.audio_duration == 20 and g.track == "song"
    # развёрнутая ссылка — для повторной загрузки музыки
    assert "/photo/7412345678901234567" in g.webpage_url


def test_instagram_carousel_has_no_audio(fake_net):
    from bot.downloader import probe_media
    fake_net["ig"] = ig_post(ig_photo(1), ig_photo(2))
    assert probe_media("https://www.instagram.com/p/C1/").audio is False


def test_slideshow_mp3_download(fake_net, http_server):
    """Штатный MP3-путь бота по /photo/-ссылке: TikTokMediaIE -> формат audio -> MP3."""
    from bot.downloader import download_video, cleanup_file
    from bot.handlers import _MUSIC_SELECTOR, _music_result
    item = tt_item()
    item["music"]["playUrl"] = f"{http_server}/music.m4a"
    item["music"]["authorName"] = "DJ Test"
    fake_net["tt"] = item
    res = download_video("https://www.tiktok.com/@traveller/photo/7412345678901234567",
                         audio_only=True, format_selector=_MUSIC_SELECTOR, audio_format="mp3")
    try:
        assert res["file_path"].endswith(".mp3")
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                              "-of", "csv=p=0", res["file_path"]], capture_output=True, text=True).stdout
        assert "mp3" in out
        view = _music_result(res)
        assert view["title"] == "song" and view["uploader"] == "DJ Test"
    finally:
        cleanup_file(res["file_path"])


class FakeAudioChat(FakeChat):
    async def send_audio(self, audio, **kw):
        self.calls.append(("audio", kw.get("title"), kw.get("performer"), kw.get("caption")))


class FakeReplyMessage(FakeMessage):
    def __init__(self, chat):
        super().__init__(chat)
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw.get("reply_markup")))


def test_offer_music_button():
    from bot import handlers
    g = Gallery(items=[], total=0, audio=True, audio_duration=20, track="song", artist="DJ")
    msg = FakeReplyMessage(FakeChat())
    asyncio.run(handlers._offer_music(msg, "https://www.tiktok.com/@u/photo/1", "TikTok", g))
    text, kb = msg.replies[0]
    assert "Музыка из слайд-шоу" in text and "song — DJ" in text and "0:20" in text
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert "MP3" in buttons[0].text and buttons[-1].text.startswith("✖️")
    sid = buttons[0].callback_data.split("|")[1]
    pending = handlers._get_pending(sid)
    assert pending.music and pending.url.endswith("/photo/1") and pending.options[0].audio_only


def _run_gallery(monkeypatch, mode, http_server, fake_net):
    from bot import handlers
    monkeypatch.setattr(handlers, "SLIDESHOW_AUDIO", mode)
    item = tt_item()
    item["music"]["playUrl"] = f"{http_server}/music.m4a"
    item["imagePost"]["images"] = [{"imageURL": {"urlList": [f"{http_server}/b.jpg"]}}] * 2
    fake_net["tt"] = item
    from bot.downloader import probe_media
    g = probe_media("https://vm.tiktok.com/ZMabc")
    chat = FakeAudioChat()
    msg = FakeReplyMessage(chat)

    async def run():
        async def no_action(*a, **k):
            return None
        chat.send_action = no_action
        await handlers._process_gallery(msg, FakeStatus(), "https://vm.tiktok.com/ZMabc", "TikTok", g, 7)
    asyncio.run(run())
    return chat, msg


def test_gallery_music_modes(monkeypatch, http_server, fake_net):
    chat, msg = _run_gallery(monkeypatch, "button", http_server, fake_net)
    assert [c[0] for c in chat.calls] == ["group"] and len(msg.replies) == 1
    chat, msg = _run_gallery(monkeypatch, "auto", http_server, fake_net)
    assert [c[0] for c in chat.calls] == ["group", "audio"] and chat.calls[1][1] == "song"
    assert not msg.replies
    chat, msg = _run_gallery(monkeypatch, "off", http_server, fake_net)
    assert [c[0] for c in chat.calls] == ["group"] and not msg.replies
    assert not [p for p in DOWNLOAD_DIR.iterdir() if not p.name.startswith(".")]


# ───────────── права (запуск не от root) ─────────────

def test_preflight_download_dir(tmp_path, monkeypatch, caplog):
    from bot import preflight
    assert preflight.check_download_dir(tmp_path)

    def deny(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(preflight.tempfile, "mkstemp", deny)
    assert not preflight.check_download_dir(tmp_path)
    assert "chown -R" in caplog.text


def test_preflight_cookies(tmp_path, monkeypatch, caplog):
    from bot import preflight
    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape")
    assert preflight.check_cookie_file("COOKIES_FILE", str(f))
    assert preflight.check_cookie_file("COOKIES_FILE", "")
    assert preflight.check_cookie_file("COOKIES_FILE", str(tmp_path / "missing.txt"))
    d = tmp_path / "dir_cookies.txt"
    d.mkdir()
    assert not preflight.check_cookie_file("COOKIES_FILE", str(d))
    monkeypatch.setattr(preflight.os, "access", lambda *a: False)
    assert not preflight.check_cookie_file("COOKIES_FILE", str(f))
    assert "не читается" in caplog.text and "папка" in caplog.text


def test_unreadable_cookies_do_not_crash(tmp_path, monkeypatch):
    from bot import downloader
    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape")
    monkeypatch.setattr(downloader, "_cookie_source", lambda url: (str(f), True))

    def deny(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(downloader.shutil, "copyfile", deny)
    with downloader._cookie_file("https://www.instagram.com/p/C1/") as c:
        assert c is None


def test_dockerfile_runs_as_app():
    text = (Path(__file__).resolve().parents[1] / "Dockerfile.python").read_text()
    assert "USER app" in text and text.index("USER app") < text.index("CMD [")


class FakeQuery:
    def __init__(self, data, chat):
        self.data, self.edits, self.deleted = data, [], False
        self.message = type("M", (), {"chat": chat,
                                      "reply_to_message": type("R", (), {"message_id": 42})()})()

    async def answer(self):
        return None

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)

    async def delete_message(self):
        self.deleted = True


def test_music_button_callback(fake_net, http_server):
    """Нажатие «🎵 Скачать музыку»: штатный handle_callback -> MP3 с названием трека."""
    from bot import handlers
    item = tt_item()
    item["music"]["playUrl"] = f"{http_server}/music.m4a"
    fake_net["tt"] = item
    g = Gallery(items=[], total=0, audio=True, audio_duration=3, track="song")
    chat = FakeAudioChat()
    msg = FakeReplyMessage(chat)
    asyncio.run(handlers._offer_music(msg, "https://www.tiktok.com/@traveller/photo/7412345678901234567",
                                      "TikTok", g))
    data = msg.replies[0][1].inline_keyboard[0][0].callback_data

    async def run():
        async def no_action(*a, **k):
            return None
        chat.send_action = no_action
        q = FakeQuery(data, chat)
        upd = type("U", (), {"callback_query": q, "effective_user": type("E", (), {"id": 9})()})()
        await handlers.handle_callback(upd, None)
        return q
    q = asyncio.run(run())
    assert q.deleted, q.edits
    assert chat.calls[0][0] == "audio" and chat.calls[0][1] == "song"
    assert not [p for p in DOWNLOAD_DIR.iterdir() if not p.name.startswith(".")]


# ───────────── новые площадки ─────────────

def _yt_dlp_ie(url):
    """Какой экстрактор возьмёт ссылку: свои (по приоритету бота), затем штатные yt-dlp."""
    from yt_dlp.extractor import gen_extractor_classes
    from bot.downloader import _CUSTOM_IES
    for ie in (*_CUSTOM_IES, *gen_extractor_classes()):
        if ie.ie_key() != "Generic" and ie.suitable(url):
            return ie.ie_key()
    return None


@pytest.mark.parametrize("raw,platform,ie", [
    ("https://dzen.ru/video/watch/6655e5b5e8c3d1325e8e8f1a", "Дзен", "ZenYandex"),
    ("https://dzen.ru/shorts/6655e5b5e8c3d1325e8e8f1a", "Дзен", "ZenYandexShorts"),
    ("https://zen.yandex.ru/video/watch/6655e5b5e8c3d1325e8e8f1a", "Дзен", "ZenYandex"),
    ("https://ok.ru/video/1234567890", "Одноклассники", "Odnoklassniki"),
    ("https://m.ok.ru/video/1234567890", "Одноклассники", "Odnoklassniki"),
    ("https://ok.ru/dk?st.cmd=movieLayer&st.mvId=1234567890", "Одноклассники", "Odnoklassniki"),
    ("https://9gag.com/gag/aXyZ123", "9GAG", "NineGag"),
    ("https://imgur.com/abc123", "Imgur", "Imgur"),
    ("https://i.imgur.com/abc123.gifv", "Imgur", "Imgur"),
    ("https://imgur.com/gallery/abc123", "Imgur", "ImgurGallery"),
    ("https://www.tumblr.com/someblog/123456789/slug", "Tumblr", "Tumblr"),
    ("https://tumblr.com/someblog/123456789", "Tumblr", "Tumblr"),
    ("https://someblog.tumblr.com/post/123456789", "Tumblr", "Tumblr"),
    ("https://streamable.com/abc12", "Streamable", "Streamable"),
    ("https://www.dailymotion.com/video/x8abcde", "Dailymotion", "Dailymotion"),
    ("https://dai.ly/x8abcde", "Dailymotion", "Dailymotion"),
    ("https://vimeo.com/123456789", "Vimeo", "VimeoNoLogin"),
    ("https://vimeo.com/123456789/abcdef1234", "Vimeo", "VimeoNoLogin"),
])
def test_new_platform_routing(raw, platform, ie):
    from bot.utils import extract_urls, identify_platform
    from bot.downloader import rejection_reason
    url = extract_urls(f"смотри {raw} !")[0]
    assert identify_platform(url) == platform
    assert rejection_reason(url) is None
    assert _yt_dlp_ie(url) == ie


def test_bare_links_new_platforms():
    from bot.utils import extract_urls
    assert extract_urls("ok.ru/video/1 и dzen.ru/shorts/abc и vimeo.com/123") == [
        "https://ok.ru/video/1", "https://dzen.ru/shorts/abc", "https://vimeo.com/123"]
    assert extract_urls("facebook.ru/x") == []      # «ok.ru» внутри чужого домена — не ссылка ОК


@pytest.mark.parametrize("url", ["https://dzen.ru/somechannel", "https://dzen.ru/a/ZxYz12abc",
                                 "https://dzen.ru/id/5a1b2c3d4e"])
def test_dzen_channel_and_article_rejected(url):
    from bot.downloader import rejection_reason
    assert "канал или статью Дзена" in rejection_reason(url)


DZEN_PAGE = ('<script>var _params = ({"ssrData": {"videoMetaResponse": {"title": "Ролик", '
             '"video": {"duration": 15}, "source": {"title": "Автор"}}}});</script>')


def test_dzen_shorts_direct_and_fallback(monkeypatch):
    import yt_dlp
    from bot.extractors import ZenYandexShortsIE
    url = "https://dzen.ru/shorts/6655e5b5e8c3d1325e8e8f1a"
    ydl = yt_dlp.YoutubeDL({"quiet": True})
    ie = ZenYandexShortsIE(ydl)

    monkeypatch.setattr(ZenYandexShortsIE, "_download_webpage", lambda self, *a, **k: DZEN_PAGE)
    info = ie._real_extract(url)
    assert info["title"] == "Ролик" and info["uploader"] == "Автор"

    monkeypatch.setattr(ZenYandexShortsIE, "_download_webpage", lambda self, *a, **k: "<html></html>")
    res = ie._real_extract(url)
    assert res["_type"] == "url" and res["url"] == "https://dzen.ru/video/watch/6655e5b5e8c3d1325e8e8f1a"
    assert res["ie_key"] == "ZenYandex"


def test_9gag_photo_meme_is_gallery(monkeypatch):
    from yt_dlp.extractor.ninegag import NineGagIE
    from bot.downloader import probe_media
    post = {"id": "aXyZ123", "type": "Photo", "title": "Смешная картинка", "creationTs": 1,
            "images": {"image700": {"url": "https://img-9gag-fun.9cache.com/photo/aXyZ123_700b.jpg",
                                    "width": 700, "height": 900},
                       "image460": {"url": "https://img-9gag-fun.9cache.com/photo/aXyZ123_460s.jpg",
                                    "width": 460, "height": 591}}}
    monkeypatch.setattr(NineGagIE, "_download_json", lambda self, *a, **k: {"data": {"post": post}})
    g = probe_media("https://9gag.com/gag/aXyZ123")
    assert isinstance(g, Gallery) and g.photos == 1
    assert g.items[0].urls[0].endswith("_700b.jpg") and g.title == "Смешная картинка"


def test_9gag_video_meme_keeps_video_flow(monkeypatch):
    from yt_dlp.extractor.ninegag import NineGagIE
    from bot.downloader import probe_media
    post = {"id": "v1", "type": "Animated", "title": "Видео-мем",
            "images": {"image460sv": {"url": "https://img-9gag-fun.9cache.com/photo/v1_460sv.mp4",
                                      "width": 460, "height": 460, "duration": 8, "hasAudio": 1,
                                      "h265Url": "https://img-9gag-fun.9cache.com/photo/v1_460svh265.mp4"}}}
    monkeypatch.setattr(NineGagIE, "_download_json", lambda self, *a, **k: {"data": {"post": post}})
    info = probe_media("https://9gag.com/gag/v1")
    assert isinstance(info, dict) and info.get("formats")


def test_imgur_plain_image_is_photo():
    raw = {"_type": "video", "id": "abc123", "extractor_key": "Imgur", "formats": [],
           "title": "meme", "thumbnails": [{"url": "https://i.imgur.com/abc123.jpeg"}]}
    g = gallery_from_info(raw, 10)
    assert g and g.items[0].urls == ["https://i.imgur.com/abc123.jpeg"]


def test_help_lists_new_platforms():
    from bot.config import SUPPORTED_PLATFORMS
    for name in ("Дзен", "Одноклассники", "9GAG", "Imgur", "Tumblr", "Streamable", "Dailymotion", "Vimeo"):
        assert name in SUPPORTED_PLATFORMS


# ───────────── Vimeo без входа ─────────────

PLAYER_CONFIG = ('{"video": {"id": 649024798, "title": "Vimeo clip", "duration": 12, '
                 '"owner": {"name": "Author"}, "thumbs": {"base": "https://i.vimeocdn.com/video/1"}}, '
                 '"request": {"files": {"progressive": [{"url": "https://vod.vimeocdn.com/720.mp4", '
                 '"quality": "720p", "width": 1280, "height": 720, "fps": 30}]}}}')


@pytest.fixture
def fake_vimeo(monkeypatch):
    from yt_dlp.extractor.vimeo import VimeoIE
    seen = []

    def page(self, url, video_id, *a, **k):
        seen.append(url)
        body = (f"<script>window.playerConfig = {PLAYER_CONFIG};</script>"
                if "player.vimeo.com" in url else "<html><body>next.js app</body></html>")
        return body, type("H", (), {"url": url})()
    monkeypatch.setattr(VimeoIE, "_download_webpage_handle", page)
    return seen


@pytest.mark.parametrize("url,player", [
    ("https://vimeo.com/649024798", "https://player.vimeo.com/video/649024798"),
    ("https://vimeo.com/649024798/abcdef1234", "https://player.vimeo.com/video/649024798?h=abcdef1234"),
])
def test_vimeo_without_login_uses_player(fake_vimeo, url, player):
    from bot.downloader import probe_media
    from bot.formats import build_options
    info = probe_media(url)
    assert player in fake_vimeo
    assert info["title"] == "Vimeo clip" and info["formats"][0]["height"] == 720
    options, _ = build_options(info, 50 * 1024 * 1024)
    assert any(o.height == 720 for o in options)


def test_vimeo_with_login_keeps_api(monkeypatch):
    import yt_dlp
    from yt_dlp.extractor.vimeo import VimeoIE
    from bot.extractors import VimeoNoLoginIE
    called = []
    monkeypatch.setattr(VimeoIE, "_extract_from_api",
                        lambda self, vid, h=None: called.append((vid, h)) or {"id": vid})
    monkeypatch.setattr(VimeoNoLoginIE, "_is_logged_in", property(lambda self: True))
    ie = VimeoNoLoginIE(yt_dlp.YoutubeDL({"quiet": True}))
    assert ie._extract_from_api("1", "abc") == {"id": "1"} and called == [("1", "abc")]


def test_vimeo_login_error_is_friendly():
    raw = ("ERROR: [vimeo] 649024798: The web client only works when logged-in. Use --cookies, "
           "--cookies-from-browser, --username and --password, --netrc-cmd, or --netrc (vimeo) to provide "
           "account credentials. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp")
    assert errors.describe(raw) == errors.AUTH

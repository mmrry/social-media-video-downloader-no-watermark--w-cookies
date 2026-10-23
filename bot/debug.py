"""
Диагностика ссылки без Telegram.

  docker compose exec bot python -m bot.debug "<url>"              # форматы и варианты бота
  docker compose exec bot python -m bot.debug "<url>" -d 0          # + скачать вариант №0 и проверить дорожки
  docker compose exec bot python -m bot.debug "<url>" -d 0 --keep   # не удалять файл после проверки
  docker compose exec bot python -m bot.debug "<url>" --json        # сырые форматы yt-dlp (JSON)
"""
import argparse
import json
import logging
import subprocess
import sys

from bot.config import MAX_FILE_SIZE_BYTES
from bot.downloader import (
    probe_media, download_video, download_gallery, cleanup_file, cleanup_by_id, first_entry,
    DownloadError, FileTooLargeError, impersonation_available,
)
from bot.gallery import Gallery
from bot.formats import build_options, ensure_dimensions, source_dims, available_ratios
from bot.media import prepare_video, ratio_label
from bot.utils import format_file_size, identify_platform, normalize_url

_FIELDS = ("format_id", "ext", "width", "height", "fps", "vcodec", "acodec", "protocol",
           "tbr", "abr", "filesize", "filesize_approx", "aspect_ratio", "format_note")


def _streams(path: str) -> list[dict]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name,width,height,channels,sample_rate,duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60, check=True).stdout
        return json.loads(out).get("streams", [])
    except Exception as e:  # noqa: BLE001 — диагностика, печатаем всё
        print(f"   ffprobe error: {e}")
        return []


def _print_streams(title: str, path: str) -> None:
    streams = _streams(path)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    print(f"\n{title}: {path}")
    for s in streams:
        if s.get("codec_type") == "video":
            print(f"   🎞  #{s['index']} video {s.get('codec_name')} {s.get('width')}x{s.get('height')}")
        elif s.get("codec_type") == "audio":
            print(f"   🔊 #{s['index']} audio {s.get('codec_name')} {s.get('channels')}ch {s.get('sample_rate')}Hz")
        else:
            print(f"   ·  #{s['index']} {s.get('codec_type')} {s.get('codec_name')}")
    print("   ✅ звук есть" if audio else "   ❌ ЗВУКА НЕТ")


def _size(f: dict) -> str:
    if f.get("filesize"):
        return format_file_size(f["filesize"])
    if f.get("filesize_approx"):
        return "≈" + format_file_size(f["filesize_approx"])
    return "-"


def _debug_gallery(url: str, g: Gallery, args) -> int:
    print(f"Фото-пост: {g.extractor_key} | {g.photos} фото, {g.videos} видео "
          f"(всего в посте {g.total}) | автор: {g.uploader!r}")
    print(f"Описание: {(g.description or g.title)[:200]!r}")
    if g.audio:
        print(f"Музыка слайд-шоу: {g.track!r} — {g.artist!r}, {g.audio_duration} с "
              f"(в боте — MP3 по SLIDESHOW_AUDIO)")
    for i, item in enumerate(g.items):
        size = f" {item.width}x{item.height}" if item.width else ""
        src = item.urls[0] if item.urls else (item.entry or {}).get("id")
        print(f"   [{i}] {item.kind}{size}: {str(src)[:120]}")
    if args.download is None:
        return 0
    print("\n=== Скачиваю весь пост")
    from bot.media import prepare_photo, image_kind, probe_image
    try:
        dl = download_gallery(url, g)
    except DownloadError as e:
        print(f"❌ {e}")
        return 1
    try:
        for f in dl.files:
            if f.kind == "photo":
                before = image_kind(f.path)
                p = prepare_photo(f.path)
                print(f"   🖼 {p.name}: {before} -> {image_kind(p)} {probe_image(p)} "
                      f"{format_file_size(p.stat().st_size)}")
            else:
                _print_streams("   🎬 видео", str(f.path))
        print(f"   не удалось: {dl.failed}")
    finally:
        if not args.keep:
            cleanup_by_id(dl.file_id)
        else:
            print(f"\nФайлы сохранены: {dl.file_id}.*")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bot link debugger")
    ap.add_argument("url")
    ap.add_argument("-d", "--download", type=int, metavar="N",
                    help="скачать вариант №N из списка бота (для фото-поста — весь пост)")
    ap.add_argument("--keep", action="store_true", help="не удалять скачанный файл")
    ap.add_argument("--json", action="store_true", help="вывести сырые форматы yt-dlp")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s", stream=sys.stdout)
    url = normalize_url(args.url)
    print(f"URL: {url}\nПлатформа бота: {identify_platform(url)}")
    print(f"curl_cffi (impersonate для Cloudflare, нужен Kick): "
          f"{'✅ есть' if impersonation_available() else '❌ НЕТ — pip install \"yt-dlp[default,curl-cffi]\"'}")

    try:
        info = probe_media(url)
    except DownloadError as e:
        print(f"❌ probe_media: {e}")
        return 1
    if isinstance(info, Gallery):
        return _debug_gallery(url, info, args)
    ensure_dimensions(info)
    entry = first_entry(info) or {}
    formats = entry.get("formats") or []
    print(f"Экстрактор: {entry.get('extractor_key')} | title: {entry.get('title')!r} | "
          f"duration: {entry.get('duration')} | entry {entry.get('width')}x{entry.get('height')}")
    print(f"yt-dlp выбрал бы по умолчанию: {entry.get('format_id')}")

    if args.json:
        print(json.dumps([{k: f.get(k) for k in _FIELDS} for f in formats], ensure_ascii=False, indent=1))
    else:
        print(f"\nФорматы ({len(formats)}):")
        print(f"   {'id':<26}{'ext':<5}{'res':<11}{'fps':<5}{'vcodec':<14}{'acodec':<12}{'proto':<16}{'tbr':<7}size")
        for f in formats:
            res = f"{f.get('width') or '?'}x{f.get('height') or '?'}"
            ac = f.get("acodec")
            mark = "  ⚠ acodec неизвестен" if ac is None and f.get("vcodec") not in (None, "none") else ""
            print(f"   {str(f.get('format_id')):<26}{str(f.get('ext')):<5}{res:<11}{str(f.get('fps') or ''):<5}"
                  f"{str(f.get('vcodec'))[:13]:<14}{str(ac)[:11]:<12}{str(f.get('protocol'))[:15]:<16}"
                  f"{str(int(f['tbr'])) if f.get('tbr') else '-':<7}{_size(f)}{mark}")

    options, hidden = build_options(info, MAX_FILE_SIZE_BYTES)
    w, h = source_dims(info)
    print(f"\nИсходник: {ratio_label(w, h)} {w}x{h} | версии: {available_ratios(options)} | скрыто: {hidden}")
    print("Варианты бота:")
    for i, o in enumerate(options):
        print(f"   [{i}] {o.label}\n       selector: {o.selector}")

    if args.download is None:
        return 0
    try:
        option = options[args.download]
    except IndexError:
        print(f"❌ Нет варианта №{args.download}")
        return 1

    print(f"\n=== Скачиваю вариант [{args.download}] {option.label}")
    path = None
    try:
        result = download_video(url, option.audio_only, option.selector, option.audio_format)
        path = result["file_path"]
        print(f"   audio_repaired: {result.get('audio_repaired')}")
        _print_streams("После yt-dlp (+ починки звука)", path)
        if not option.audio_only:
            prepared = prepare_video(path)
            path = str(prepared.path)
            _print_streams("После prepare_video (то, что уходит в Telegram)", path)
            print(f"   meta: {prepared.meta} | thumb: {bool(prepared.thumbnail)}")
    except (DownloadError, FileTooLargeError) as e:
        print(f"❌ {type(e).__name__}: {e}")
        return 1
    finally:
        if path and not args.keep:
            cleanup_file(path)
        elif path:
            print(f"\nФайл сохранён: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

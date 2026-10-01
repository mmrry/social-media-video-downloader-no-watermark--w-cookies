"""
Подготовка видео к отправке в Telegram.

Почему в Telegram «чёрный квадрат»:
  * Bot API не знает width/height -> клиент рисует квадрат по умолчанию;
  * нет thumbnail -> нет превью;
  * moov-атом в конце файла (нет faststart) и/или кодек VP9/AV1/HEVC ->
    Telegram не может сам сгенерировать превью и стриминг.

Здесь: ffprobe (размеры с учётом поворота), faststart-remux,
опциональный транскод в H.264 и генерация JPEG-превью <=320px, <200KB.
"""
import contextlib
import json
import logging
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bot.config import TRANSCODE_NON_H264, TRANSCODE_MAX_MB
from bot.downloader import has_free_space

logger = logging.getLogger(__name__)

THUMB_MAX_SIDE = 320
THUMB_MAX_BYTES = 200 * 1024
TG_FRIENDLY_VCODECS = {"h264"}

REMUX_ARGS = ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
TRANSCODE_ARGS = [
    "-map", "0:v:0", "-map", "0:a:0?",
    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k",
]


@dataclass
class VideoMeta:
    width: int = 0
    height: int = 0
    duration: int = 0
    vcodec: str = ""


_COMMON_RATIOS = [(9, 16), (16, 9), (1, 1), (4, 5), (4, 3), (3, 4), (21, 9), (2, 3), (3, 2)]


def ratio_label(width: int, height: int) -> str:
    """1920x1080 -> '16:9', 1080x1350 -> '4:5', иначе '1.85:1'."""
    if not width or not height:
        return "?"
    r = width / height
    for rw, rh in _COMMON_RATIOS:
        if abs(r - rw / rh) / (rw / rh) < 0.03:
            return f"{rw}:{rh}"
    return f"{r:.2f}:1"


def probe_url(url: str, headers: dict | None = None, timeout: int = 20) -> tuple[int, int]:
    """
    Реальные размеры кадра (с учётом поворота) по ссылке на поток/файл — читаются
    только заголовки. Для площадок, которые не отдают width/height в метаданных.
    """
    cmd = ["ffprobe", "-v", "error", "-rw_timeout", str(timeout * 1_000_000)]
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd += ["-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
            "-of", "json", url]
    try:
        data = json.loads(_run(cmd, timeout + 5).stdout or "{}")
        s = (data.get("streams") or [{}])[0]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, IndexError) as e:
        logger.info("probe_url failed: %s", e)
        return 0, 0
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    rotation = 0
    try:
        rotation = int(float((s.get("tags") or {}).get("rotate", 0)))
    except (TypeError, ValueError):
        pass
    for sd in s.get("side_data_list") or []:
        if "rotation" in sd:
            with contextlib.suppress(TypeError, ValueError):
                rotation = int(float(sd["rotation"]))
    return (h, w) if abs(rotation) % 180 == 90 else (w, h)


@dataclass
class PreparedVideo:
    path: Path
    meta: VideoMeta
    thumbnail: Path | None


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)


def probe(path: Path) -> VideoMeta:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,duration:stream_tags=rotate:"
        "stream_side_data=rotation:format=duration",
        "-of", "json", str(path),
    ]
    try:
        data = json.loads(_run(cmd, 60).stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:
        logger.warning("ffprobe failed for %s: %s", path, e)
        return VideoMeta()

    streams = data.get("streams") or []
    if not streams:
        return VideoMeta()
    s = streams[0]
    width, height = int(s.get("width") or 0), int(s.get("height") or 0)

    # Вертикальные видео с телефонов часто хранятся как 1920x1080 + rotate=90
    rotation = 0
    try:
        rotation = int(float((s.get("tags") or {}).get("rotate", 0)))
    except (TypeError, ValueError):
        pass
    for sd in s.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if abs(rotation) % 180 == 90:
        width, height = height, width

    raw_dur = s.get("duration") or (data.get("format") or {}).get("duration") or 0
    try:
        duration = int(float(raw_dur))
    except (TypeError, ValueError):
        duration = 0

    return VideoMeta(width, height, duration, (s.get("codec_name") or "").lower())


def _moov_before_mdat(path: Path) -> bool:
    """True, если файл уже faststart (moov раньше mdat). Читаются только заголовки атомов."""
    try:
        with open(path, "rb") as f:
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return False
                size, typ = struct.unpack(">I4s", hdr)
                if typ == b"moov":
                    return True
                if typ == b"mdat":
                    return False
                if size == 1:  # 64-битный размер
                    ext = f.read(8)
                    if len(ext) < 8:
                        return False
                    f.seek(struct.unpack(">Q", ext)[0] - 16, 1)
                elif size < 8:
                    return False
                else:
                    f.seek(size - 8, 1)
    except OSError:
        return False


def _ffmpeg_to(src: Path, dst: Path, args: list[str], timeout: int) -> bool:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), *args,
           "-movflags", "+faststart", str(dst)]
    try:
        _run(cmd, timeout)
        return dst.exists() and dst.stat().st_size > 0
    except subprocess.CalledProcessError as e:
        logger.warning("ffmpeg failed (%s): %s", src.name, (e.stderr or "").strip()[-500:])
    except subprocess.SubprocessError as e:
        logger.warning("ffmpeg failed (%s): %s", src.name, e)
    dst.unlink(missing_ok=True)
    return False


def make_thumbnail(path: Path, duration: int) -> Path | None:
    """JPEG-превью по требованиям Bot API: <=320px по большей стороне, <200 KB."""
    out = path.with_name(f"{path.stem}.thumb.jpg")
    positions = ([min(duration * 0.1, 3.0)] if duration else []) + [0.0]
    vf = f"scale={THUMB_MAX_SIDE}:{THUMB_MAX_SIDE}:force_original_aspect_ratio=decrease"

    for ts in positions:
        for q in (4, 8, 15):
            out.unlink(missing_ok=True)
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}", "-i", str(path),
                   "-frames:v", "1", "-vf", vf, "-q:v", str(q), str(out)]
            try:
                _run(cmd, 60)
            except subprocess.SubprocessError:
                break  # пробуем другую позицию
            if out.exists() and 0 < out.stat().st_size <= THUMB_MAX_BYTES:
                return out
    out.unlink(missing_ok=True)
    return None


def make_cover(image_path: str | None) -> bytes | None:
    """Обложка аудио для Telegram: JPEG <=320px, <200 KB. Синхронно (to_thread)."""
    if not image_path or not Path(image_path).is_file():
        return None
    src = Path(image_path)
    out = src.with_name(f"{src.stem}.cover.jpg")
    vf = f"scale={THUMB_MAX_SIDE}:{THUMB_MAX_SIDE}:force_original_aspect_ratio=decrease"
    try:
        for q in (3, 6, 12):
            out.unlink(missing_ok=True)
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-frames:v", "1",
                  "-vf", vf, "-q:v", str(q), str(out)], 30)
            if out.exists() and 0 < out.stat().st_size <= THUMB_MAX_BYTES:
                return out.read_bytes()
    except subprocess.SubprocessError as e:
        logger.warning("cover conversion failed: %s", e)
    finally:
        out.unlink(missing_ok=True)
    return None


def _replace_with(path: Path, tmp: Path) -> Path:
    final = path.with_suffix(".mp4")
    if final != path:
        path.unlink(missing_ok=True)
    tmp.replace(final)
    return final


def prepare_video(file_path: str) -> PreparedVideo:
    """Синхронная функция — вызывать через asyncio.to_thread."""
    path = Path(file_path)
    try:
        meta = probe(path)
        size = path.stat().st_size


        need_transcode = (
            TRANSCODE_NON_H264
            and meta.vcodec
            and meta.vcodec not in TG_FRIENDLY_VCODECS
            and size <= TRANSCODE_MAX_MB * 1024 * 1024
        )
        need_remux = path.suffix.lower() != ".mp4" or not _moov_before_mdat(path)

        if (need_transcode or need_remux) and has_free_space(size):
            tmp = path.with_name(f"{path.stem}.tg.mp4")
            args, timeout = (TRANSCODE_ARGS, 3600) if need_transcode else (REMUX_ARGS, 600)
            logger.info("%s %s (vcodec=%s)", "Transcoding" if need_transcode else "Remuxing",
                        path.name, meta.vcodec)
            if _ffmpeg_to(path, tmp, args, timeout):
                path = _replace_with(path, tmp)
                meta = probe(path)

        return PreparedVideo(path, meta, make_thumbnail(path, meta.duration))
    except Exception:
        logger.exception("prepare_video failed, sending as is")
        return PreparedVideo(path, VideoMeta(), None)

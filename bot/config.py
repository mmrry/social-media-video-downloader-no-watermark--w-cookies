import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- Bot Settings ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set. Please create a .env file with your bot token.")

# Admin user IDs (comma-separated in .env, e.g. "123456,789012")
_admin_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]

# Cookies (Netscape format). Instagram — обязательно; VK — опционально
# (нужны для приватных/18+ видео и если VK начинает требовать вход)
COOKIES_FILE: str = os.getenv("COOKIES_FILE", "")
VK_COOKIES_FILE: str = os.getenv("VK_COOKIES_FILE", "")

# --- Network ---
# Прокси для yt-dlp: http://host:port, socks5://user:pass@host:port
PROXY: str = os.getenv("PROXY", "").strip()          # для всех платформ
VK_PROXY: str = os.getenv("VK_PROXY", "").strip()    # только vk.com / vk.ru / vkvideo.ru (перекрывает PROXY)
# Принудительно IPv4 (если IPv6 в контейнере «чёрная дыра» и коннекты висят до таймаута)
FORCE_IPV4: bool = _bool("FORCE_IPV4", False)
# Куда слать API-запросы VK-экстракторов yt-dlp вместо vk.com (например vk.ru)
VK_API_HOST: str = os.getenv("VK_API_HOST", "").strip()
# Таймаут сокета yt-dlp, сек
SOCKET_TIMEOUT: int = int(os.getenv("SOCKET_TIMEOUT", "30"))

# LOCAL BOT API
BOT_API_URL: str = os.getenv("BOT_API_URL", "").strip()
# local_mode: бот передаёт серверу путь к файлу вместо HTTP-загрузки.
# Требует TELEGRAM_LOCAL=1 у telegram-bot-api и одинаковый путь к downloads в обоих контейнерах.
LOCAL_MODE: bool = _bool("TELEGRAM_LOCAL_MODE", default=bool(BOT_API_URL))

# --- Download Settings ---
_default_limit = "2000" if BOT_API_URL else "50"
MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", _default_limit))
MAX_FILE_SIZE_BYTES: int = MAX_FILE_SIZE_MB * 1024 * 1024

# Порог предупреждения «файл больше N МБ»
WARNING_THRESHOLD_BYTES: int = int(os.getenv("WARNING_THRESHOLD_MB", "1024")) * 1024 * 1024

# Всегда оставлять свободным на диске
DISK_RESERVE_BYTES: int = int(os.getenv("DISK_RESERVE_MB", "100")) * 1024 * 1024

# Абсолютный путь обязателен для local_mode (file:// URI)
DOWNLOAD_DIR: Path = Path(os.getenv("DOWNLOAD_DIR", "./downloads")).resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --- Video compatibility ---
# Предпочитать H.264 при выборе формата (превью и воспроизведение во всех клиентах)
PREFER_H264: bool = _bool("PREFER_H264", True)
# Перекодировать в H.264, если пришёл VP9/AV1/HEVC (только файлы до TRANSCODE_MAX_MB)
TRANSCODE_NON_H264: bool = _bool("TRANSCODE_NON_H264", True)
TRANSCODE_MAX_MB: int = int(os.getenv("TRANSCODE_MAX_MB", "300"))

# Cooldown between requests per user (seconds)
COOLDOWN_SECONDS: int = int(os.getenv("COOLDOWN_SECONDS", "5"))

# Max simultaneous downloads across all users
MAX_CONCURRENT_DOWNLOADS: int = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

# Сколько живут кнопки выбора формата (сек)
PENDING_URL_TTL: int = int(os.getenv("PENDING_URL_TTL", "3600"))

# --- Supported Platforms ---
SUPPORTED_PLATFORMS: dict[str, list[str]] = {
    "TikTok":      ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
    "Instagram":   ["instagram.com"],  # COOKIES.txt
    "Facebook":    ["facebook.com", "fb.watch", "fb.com"],
    "Pinterest":   ["pinterest.com", "pin.it"],
    "X (Twitter)": ["twitter.com", "x.com"],
    "YouTube":     ["youtube.com", "youtu.be", "m.youtube.com"],
    # "Reddit":    ["reddit.com", "redd.it", "v.redd.it"],  # Error 403, need login cookies
    "Snapchat":    ["snapchat.com", "t.snapchat.com"],
    "Twitch":      ["twitch.tv", "clips.twitch.tv", "m.twitch.tv"],  # только клипы; записи (VOD) отключены
    # Порядок важен: live.vkvideo.ru должен совпасть раньше, чем vkvideo.ru
    "VK Video Live": ["live.vkvideo.ru", "live.vkplay.ru", "vkplay.live"],  # клипы (моменты), записи
    "VK":          ["vk.com", "vk.ru", "vkvideo.ru"],  # видео и клипы
    "RuTube":      ["rutube.ru"],
    "SoundCloud":  ["soundcloud.com", "on.soundcloud.com", "m.soundcloud.com"],  # только аудио
    "Kick":        ["kick.com"],  # только клипы (/clips/clip_…, ?clip=clip_…); записи отключены; нужен curl_cffi
    # "Threads":   ["threads.net", "threads.com"],  # yt-dlp not supported threads.com
}

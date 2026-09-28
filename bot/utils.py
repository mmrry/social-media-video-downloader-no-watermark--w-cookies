import re
import os
from urllib.parse import urlparse
from bot.config import SUPPORTED_PLATFORMS


# URL со схемой
URL_REGEX = re.compile(
    r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}"
    r"\b[-a-zA-Z0-9()@:%_\+.~#?&//=]*",
    re.IGNORECASE,
)

# URL без схемы, но только для поддерживаемых доменов: «vk.ru/clip1_2», «m.tiktok.com/...»
_ALL_DOMAINS = sorted({d for ds in SUPPORTED_PLATFORMS.values() for d in ds}, key=len, reverse=True)
BARE_URL_REGEX = re.compile(
    r"(?<![\w@/.:-])(?:www\.)?(?:[\w-]+\.)*(?:"
    + "|".join(re.escape(d) for d in _ALL_DOMAINS)
    + r")/[^\s<>\"'«»]+",
    re.IGNORECASE,
)

_TRAILING_PUNCT = ".,;:!?)]}»\"'"


def extract_urls(text: str) -> list[str]:
    """Extract all distinct normalized URLs (with or without scheme) from text."""
    found = URL_REGEX.findall(text)
    # Схемные URL вырезаем, чтобы bare-регулярка не нашла их хвосты повторно
    rest = URL_REGEX.sub(" ", text)
    found += [f"https://{u}" for u in BARE_URL_REGEX.findall(rest)]

    seen = set()
    unique_urls = []
    for url in found:
        url = url.rstrip(_TRAILING_PUNCT)
        norm = normalize_url(url)
        if norm not in seen:
            seen.add(norm)
            unique_urls.append(norm)
    return unique_urls


# Хосты, у которых query — только трекинг (?share=..., ?utm=...)
_STRIP_QUERY_HOSTS = ("tiktok.com", "live.vkvideo.ru", "live.vkplay.ru", "vkplay.live")


def normalize_url(url: str) -> str:
    """Strip common tracking parameters from URLs."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").removeprefix("www.")
        if any(host == h or host.endswith(f".{h}") for h in _STRIP_QUERY_HOSTS):
            return f"{parsed.scheme}://{parsed.hostname}{parsed.path.rstrip('/')}"
        # vk.com/clips-123?z=clip-123_456 -> прямая ссылка на клип
        if host.endswith(("vk.com", "vk.ru", "vkvideo.ru")):
            m = re.search(r"[?&]z=(clip|video)(-?\d+_\d+)", url)
            if m:
                return f"https://vkvideo.ru/{m.group(1)}{m.group(2)}"
        return url
    except Exception:
        return url


def sanitize_filename(filename: str) -> str:
    """Remove characters that are unsafe for filenames."""
    # Keep alphanumeric, spaces, and basic punctuation
    sanitized = re.sub(r'[^\w\s\-\.]', '', filename)
    return sanitized.strip()[:100]  # Cap length


def identify_platform(url: str) -> str | None:
    """
    Identify which supported platform a URL belongs to.
    Returns the platform name or None if unsupported.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    # Strip leading "www."
    hostname = hostname.removeprefix("www.")

    for platform, domains in SUPPORTED_PLATFORMS.items():
        for domain in domains:
            if hostname == domain or hostname.endswith(f".{domain}"):
                return platform
    return None


def _escape_html(text: str | None) -> str:
    """Escape HTML special characters for Telegram messages."""
    return (
        (text or "").replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_file_size(size_bytes: int) -> str:
    """Format byte size into a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def get_file_size(file_path: str) -> int:
    """Get file size in bytes."""
    try:
        return os.path.getsize(file_path)
    except OSError:
        return 0

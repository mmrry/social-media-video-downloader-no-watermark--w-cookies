"""
Проверка прав при старте (бот работает в контейнере не от root).

Типичная ошибка после переезда: ./downloads или cookies.txt на хосте принадлежат root
(chmod 600), а бот — пользователь app с UID 1000. Без проверки это всплывает только
на первой загрузке непонятной ошибкой; здесь — сразу и с готовой командой исправления.
"""
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _ids() -> str:
    return f"{os.getuid()}:{os.getgid()}"


def check_download_dir(path: Path) -> bool:
    """Можно ли писать в папку загрузок. False — работать нельзя."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".write-test-", dir=path)
        os.close(fd)
        os.unlink(tmp)
        return True
    except OSError as e:
        logger.critical(
            "Нет прав на запись в %s (%s). Бот работает как UID:GID %s. "
            "Исправление на хосте: sudo chown -R %s ./downloads && sudo chmod 755 ./downloads "
            "(или задайте APP_UID/APP_GID в .env и пересоберите образ).",
            path, e.strerror or e, _ids(), _ids())
        return False


def check_cookie_file(name: str, path: str) -> bool:
    """Читается ли файл cookies. False — платформа будет работать без авторизации."""
    if not path:
        return True
    p = Path(path)
    if p.is_dir():
        logger.error(
            "%s=%s — это папка, а не файл: Docker создал её, потому что файла на хосте не было. "
            "Исправление: docker compose down && rmdir %s && touch %s && sudo chown %s %s && chmod 600 %s",
            name, path, p.name, p.name, _ids(), p.name, p.name)
        return False
    if not p.exists():
        return True  # не настроено — downloader сам предупредит при запросе
    if not os.access(p, os.R_OK):
        logger.error(
            "%s=%s не читается ботом (UID:GID %s) — запросы пойдут без cookies. "
            "Исправление на хосте: sudo chown %s %s && chmod 600 %s",
            name, path, _ids(), _ids(), p.name, p.name)
        return False
    return True


def run_checks(download_dir: Path, cookie_files: dict[str, str]) -> bool:
    ok = check_download_dir(download_dir)
    for name, path in cookie_files.items():
        check_cookie_file(name, path)
    return ok

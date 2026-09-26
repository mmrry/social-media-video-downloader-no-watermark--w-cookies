"""
Async download queue manager.
Semaphores are created on first access within the running event loop.
"""
import asyncio
from bot.config import MAX_CONCURRENT_DOWNLOADS

_MAX_PER_USER = 1

_global_sem: asyncio.Semaphore | None = None
_user_sems: dict[int, asyncio.Semaphore] = {}
_active_count: int = 0
_waiting_count: int = 0


def _global() -> asyncio.Semaphore:
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return _global_sem


def _user(user_id: int) -> asyncio.Semaphore:
    sem = _user_sems.get(user_id)
    if sem is None:
        sem = _user_sems[user_id] = asyncio.Semaphore(_MAX_PER_USER)
    return sem


async def acquire(user_id: int) -> None:
    global _active_count, _waiting_count
    _waiting_count += 1
    user_sem = _user(user_id)
    try:
        await user_sem.acquire()
        try:
            await _global().acquire()
        except BaseException:
            user_sem.release()  # не оставляем «висящий» слот при отмене
            raise
    finally:
        _waiting_count -= 1
    _active_count += 1


async def release(user_id: int) -> None:
    global _active_count
    _global().release()
    sem = _user_sems.get(user_id)
    if sem is not None:
        sem.release()
        if not sem.locked():  # свободен и нет ожидающих -> не копим словарь
            _user_sems.pop(user_id, None)
    _active_count = max(0, _active_count - 1)


def active_downloads() -> int:
    return _active_count


def queue_depth() -> int:
    """Сколько запросов ждут слот (раньше тут фактически возвращалось число активных)."""
    return _waiting_count

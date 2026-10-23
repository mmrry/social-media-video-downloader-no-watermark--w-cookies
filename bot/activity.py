"""
Ежедневный отчёт админам: кто пользовался ботом и какие ссылки отправлял.

Включается DAILY_REPORT=1. Каждая ссылка из сообщения дописывается строкой JSON
в файл дня DATA_DIR/activity/YYYY-MM-DD.jsonl (день — по DAILY_REPORT_TZ), поэтому
перезапуск бота посреди дня ничего не теряет. Раз в сутки в DAILY_REPORT_TIME отчёт
за прошедший день уходит в личку каждому из ADMIN_IDS. Если в это время бот был
выключен — отчёт уйдёт сразу после старта (дата последней отправки — в report_state.json).

Ограничения (ресурсы VPS и приватность):
  * файл дня не больше ACTIVITY_MAX_BYTES — дальше ссылки только считаются;
  * хранятся файлы за сегодня и вчера, старше — удаляются.
"""
import asyncio
import contextlib
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.constants import ParseMode
from telegram.error import TelegramError

from bot.utils import _escape_html

logger = logging.getLogger(__name__)

ACTIVITY_MAX_BYTES = 5 * 1024 * 1024   # на один день
KEEP_DAYS = 2                          # сегодня + вчера
MSG_LIMIT = 4000                       # Telegram: 4096 символов на сообщение
MAX_MESSAGES = 5                       # длиннее — отчёт уходит файлом
URL_DISPLAY_LIMIT = 300


def parse_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("DAILY_REPORT_TZ=%r не найден — используется UTC", name)
        return ZoneInfo("UTC")


def parse_time(value: str) -> dtime:
    try:
        hh, mm = (value or "").strip().split(":")
        return dtime(int(hh), int(mm))
    except ValueError:
        logger.warning("DAILY_REPORT_TIME=%r — ожидается ЧЧ:ММ, используется 09:00", value)
        return dtime(9, 0)


# ───────────────────────────── запись ─────────────────────────────

class ActivityLog:
    def __init__(self, data_dir: Path, tz: ZoneInfo):
        self.dir = data_dir / "activity"
        self.tz = tz
        self.enabled = True
        self._overflow: dict[str, int] = {}   # день -> сколько ссылок не записано (лимит)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".write-test"
            probe.write_text("")
            probe.unlink()
        except OSError as e:
            self.enabled = False
            logger.error("Отчёт админам выключен: нет доступа к %s (%s). "
                         "Исправление на хосте: sudo chown -R %s ./data", self.dir, e,
                         f"{os.getuid()}:{os.getgid()}")

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def day_file(self, day: date) -> Path:
        return self.dir / f"{day.isoformat()}.jsonl"

    def record(self, user, chat, url: str, platform: str | None) -> None:
        """Синхронно и быстро (одна строка в файл) — вызывается из обработчика сообщений."""
        if not self.enabled or user is None:
            return
        now = datetime.now(self.tz)
        path = self.day_file(now.date())
        try:
            if path.exists() and path.stat().st_size >= ACTIVITY_MAX_BYTES:
                key = now.date().isoformat()
                self._overflow[key] = self._overflow.get(key, 0) + 1
                return
            row = {
                "t": now.strftime("%H:%M"),
                "uid": user.id,
                "name": user.full_name or "",
                "username": user.username or "",
                "url": url[:2000],
                "platform": platform or "",
            }
            if chat is not None and chat.type != "private":
                row["chat"] = chat.title or str(chat.id)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("activity log write failed: %s", e)

    def load(self, day: date) -> list[dict]:
        path = self.day_file(day)
        rows = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    with contextlib.suppress(json.JSONDecodeError):
                        rows.append(json.loads(line))
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("activity log read failed: %s", e)
        return rows

    def overflow(self, day: date) -> int:
        return self._overflow.get(day.isoformat(), 0)

    def cleanup(self) -> None:
        """Удалить файлы старше KEEP_DAYS дней."""
        if not self.enabled:
            return
        oldest = self.today() - timedelta(days=KEEP_DAYS - 1)
        for p in self.dir.glob("*.jsonl"):
            with contextlib.suppress(ValueError, OSError):
                if date.fromisoformat(p.stem) < oldest:
                    p.unlink()


# ───────────────────────────── отчёт ─────────────────────────────

@dataclass
class Report:
    messages: list[str]          # HTML-сообщения для Telegram
    text: str                    # то же простым текстом — если отчёт уходит файлом
    users: int
    links: int


def _user_title(row: dict, html: bool) -> str:
    esc = _escape_html if html else (lambda x: x)
    name = esc(row.get("name") or "Без имени")
    if row.get("username"):
        name += f" (@{esc(row['username'])})"
    uid = f"<code>{row['uid']}</code>" if html else str(row["uid"])
    return f"{name} · {uid}"


def build_report(day: date, rows: list[dict], tz_name: str, overflow: int = 0) -> Report:
    by_user: OrderedDict[int, list[dict]] = OrderedDict()
    for r in rows:
        if "uid" in r:
            by_user.setdefault(r["uid"], []).append(r)
    # Самые активные — сверху
    users = sorted(by_user.values(), key=len, reverse=True)
    total = sum(len(u) for u in users)

    head = f"📊 Отчёт за {day.strftime('%d.%m.%Y')} ({tz_name})"
    summary = f"Пользователей: {len(users)} · ссылок: {total}"
    if overflow:
        summary += f" (+{overflow} не записано: лимит журнала)"
    if not users:
        summary = "Ботом никто не пользовался."

    html_lines = [f"<b>{_escape_html(head)}</b>", summary]
    text_lines = [head, summary]
    for entries in users:
        first = entries[0]
        html_lines += ["", f"👤 <b>{_user_title(first, True)}</b> — {len(entries)}"]
        text_lines += ["", f"{_user_title(first, False)} — {len(entries)}"]
        for e in entries:
            url = e.get("url", "")
            short = url if len(url) <= URL_DISPLAY_LIMIT else url[:URL_DISPLAY_LIMIT - 1] + "…"
            where = f" · {e['chat']}" if e.get("chat") else ""
            platform = e.get("platform") or "не поддерживается"
            html_lines.append(f"{e.get('t', '')} {_escape_html(platform)}{_escape_html(where)}\n"
                              f"{_escape_html(short)}")
            text_lines.append(f"  {e.get('t', '')} {platform}{where}  {url}")

    messages, cur = [], ""
    for line in html_lines:
        add = (("\n" if cur else "") + line)
        if len(cur) + len(add) > MSG_LIMIT and cur:
            messages.append(cur)
            cur = line[:MSG_LIMIT]
        else:
            cur += add
    if cur:
        messages.append(cur)
    return Report(messages, "\n".join(text_lines) + "\n", len(users), total)


async def send_report(bot, admin_ids: list[int], report: Report, day: date) -> int:
    """Отправить отчёт каждому админу. Возвращает, скольким удалось."""
    sent = 0
    for admin in admin_ids:
        try:
            if len(report.messages) > MAX_MESSAGES:
                # Слишком длинно для чата — сводка + файл
                await bot.send_message(admin, report.messages[0].split("\n\n")[0],
                                       parse_mode=ParseMode.HTML)
                await bot.send_document(admin, document=report.text.encode("utf-8"),
                                        filename=f"report-{day.isoformat()}.txt")
            else:
                for m in report.messages:
                    await bot.send_message(admin, m, parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)
            sent += 1
        except TelegramError as e:
            # Чаще всего админ не нажимал /start у бота — писать первым бот не может
            logger.warning("Daily report to %s failed: %s (админ должен написать боту /start)", admin, e)
    return sent


# ───────────────────────────── расписание ─────────────────────────────

def report_due(now: datetime, at: dtime, last_sent: date | None) -> date | None:
    """
    За какой день пора отправить отчёт (None — пока не пора).
    Отчёт за день D уходит в `at` дня D+1; пропущенный — при первой проверке после.
    Отправляется только последний прошедший день, без «хвоста» за неделю простоя.
    """
    target = now.date() - timedelta(days=1 if now.time() >= at else 2)
    if last_sent is None or last_sent < target:
        return target
    return None


def seconds_until(now: datetime, at: dtime) -> float:
    nxt = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


class ReportState:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "report_state.json"

    def load(self) -> date | None:
        try:
            return date.fromisoformat(json.loads(self.path.read_text())["last_sent"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, day: date) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"last_sent": day.isoformat()}))
            tmp.replace(self.path)
        except OSError as e:
            logger.warning("report state write failed: %s", e)


async def report_once(bot, log: ActivityLog, state: ReportState, admin_ids: list[int],
                      at: dtime, tz_name: str, now: datetime | None = None) -> date | None:
    """Одна проверка расписания: отправить отчёт, если пора. Возвращает отправленный день."""
    now = now or datetime.now(log.tz)
    last = state.load()
    if last is None:
        # Первый запуск: отсчёт с этого момента, без отчёта за день до установки
        state.save(report_due(now, at, None))
        return None
    day = report_due(now, at, last)
    if day is None:
        return None
    report = build_report(day, log.load(day), tz_name, log.overflow(day))
    sent = await send_report(bot, admin_ids, report, day)
    logger.info("Daily report for %s: %d users, %d links -> %d/%d admins",
                day, report.users, report.links, sent, len(admin_ids))
    if sent:
        state.save(day)
        log.cleanup()
        return day
    return None           # никому не дошло (сеть, админ не нажал /start) — повтор через час


async def report_loop(bot, log: ActivityLog, state: ReportState, admin_ids: list[int],
                      at: dtime, tz_name: str) -> None:
    while True:
        try:
            await report_once(bot, log, state, admin_ids, at, tz_name)
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            logger.exception("daily report failed")
        # Не дольше часа: переживает смену часового пояса / перевод часов
        await asyncio.sleep(min(seconds_until(datetime.now(log.tz), at) + 5, 3600))

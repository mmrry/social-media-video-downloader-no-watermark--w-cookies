"""Ежедневный отчёт админам (bot/activity.py)."""
import asyncio
from datetime import date, datetime, time as dtime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest
from telegram.error import Forbidden, NetworkError

from bot import activity

TZ = ZoneInfo("Europe/Moscow")
IVAN = NS(id=111, full_name="Иван <Петров>", username="ivan")
ANNA = NS(id=222, full_name="Anna", username=None)
PRIVATE = NS(type="private", title=None, id=111)
GROUP = NS(type="supergroup", title="Чат друзей", id=-100)


@pytest.fixture
def log(tmp_path):
    return activity.ActivityLog(tmp_path, TZ)


class FakeBot:
    def __init__(self, fail=()):
        self.sent, self.fail = [], dict(fail)

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.fail:
            raise self.fail[chat_id]
        self.sent.append((chat_id, "msg", text))

    async def send_document(self, chat_id, document, filename=None, **kw):
        self.sent.append((chat_id, "doc", filename, document))


def test_record_and_load(log):
    log.record(IVAN, PRIVATE, "https://www.tiktok.com/@u/video/1", "TikTok")
    log.record(IVAN, GROUP, "https://youtu.be/abc", "YouTube")
    log.record(ANNA, PRIVATE, "https://example.com/x", None)
    rows = log.load(log.today())
    assert [r["uid"] for r in rows] == [111, 111, 222]
    assert "chat" not in rows[0] and rows[1]["chat"] == "Чат друзей"
    assert rows[2]["platform"] == ""


def test_size_cap(log, monkeypatch):
    monkeypatch.setattr(activity, "ACTIVITY_MAX_BYTES", 300)
    for i in range(10):
        log.record(IVAN, PRIVATE, f"https://youtu.be/{i}", "YouTube")
    day = log.today()
    assert 0 < len(log.load(day)) < 10
    assert log.overflow(day) == 10 - len(log.load(day))
    assert "не записано" in activity.build_report(day, log.load(day), "X", log.overflow(day)).messages[0]


def test_cleanup_keeps_today_and_yesterday(log):
    today = log.today()
    for d in range(4):
        log.day_file(today - timedelta(days=d)).write_text("{}\n")
    log.cleanup()
    assert sorted(p.stem for p in log.dir.glob("*.jsonl")) == sorted(
        (today - timedelta(days=d)).isoformat() for d in range(2))


def test_unwritable_dir_disables(tmp_path, monkeypatch):
    def deny(self, *a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(activity.Path, "write_text", deny)
    log = activity.ActivityLog(tmp_path, TZ)
    assert not log.enabled
    log.record(IVAN, PRIVATE, "https://youtu.be/x", "YouTube")   # не падает


def test_build_report_format():
    rows = [
        {"t": "10:15", "uid": 222, "name": "Anna", "username": "", "url": "https://a/1", "platform": "Vimeo"},
        {"t": "11:00", "uid": 111, "name": "Иван <Петров>", "username": "ivan",
         "url": "https://x/?a=1&b=2", "platform": "TikTok"},
        {"t": "12:00", "uid": 111, "name": "Иван <Петров>", "username": "ivan",
         "url": "https://example.com", "platform": "", "chat": "Чат друзей"},
    ]
    r = activity.build_report(date(2026, 10, 22), rows, "Europe/Moscow")
    text = "\n".join(r.messages)
    assert r.users == 2 and r.links == 3 and len(r.messages) == 1
    assert "Отчёт за 22.10.2026 (Europe/Moscow)" in text
    assert text.index("Иван") < text.index("Anna")             # активные сверху
    assert "&lt;Петров&gt;" in text and "a=1&amp;b=2" in text   # HTML экранирован
    assert "(@ivan) · <code>111</code></b> — 2" in text
    assert "не поддерживается · Чат друзей" in text
    assert "https://x/?a=1&b=2" in r.text                       # файл — без экранирования


def test_build_report_empty():
    r = activity.build_report(date(2026, 10, 22), [], "UTC")
    assert "никто не пользовался" in r.messages[0] and r.users == 0


def test_build_report_splits_long():
    rows = [{"t": "10:00", "uid": i, "name": f"user{i}", "username": "", "url": "https://youtu.be/" + "x" * 200,
             "platform": "YouTube"} for i in range(60)]
    r = activity.build_report(date(2026, 10, 22), rows, "UTC")
    assert len(r.messages) > 1 and all(len(m) <= 4096 for m in r.messages)


AT = dtime(9, 0)


@pytest.mark.parametrize("now,last,expected", [
    (datetime(2026, 10, 23, 8, 59, tzinfo=TZ), date(2026, 10, 21), None),               # ещё рано
    (datetime(2026, 10, 23, 9, 0, tzinfo=TZ), date(2026, 10, 21), date(2026, 10, 22)),  # пора
    (datetime(2026, 10, 23, 15, 0, tzinfo=TZ), date(2026, 10, 22), None),               # уже отправлен
    (datetime(2026, 10, 30, 15, 0, tzinfo=TZ), date(2026, 10, 20), date(2026, 10, 29)), # простой: только последний
    (datetime(2026, 10, 23, 8, 0, tzinfo=TZ), date(2026, 10, 20), date(2026, 10, 21)),  # пропущенный вчерашний
])
def test_report_due(now, last, expected):
    assert activity.report_due(now, AT, last) == expected


def test_seconds_until():
    assert activity.seconds_until(datetime(2026, 10, 23, 8, 0, tzinfo=TZ), AT) == 3600
    assert activity.seconds_until(datetime(2026, 10, 23, 9, 0, tzinfo=TZ), AT) == 24 * 3600


def test_report_once_flow(tmp_path, log):
    state = activity.ReportState(tmp_path)
    bot = FakeBot(fail={333: Forbidden("bot can't initiate conversation")})
    admins = [1, 333]
    d1 = datetime(2026, 10, 22, 12, 0, tzinfo=TZ)

    # первый запуск: без отчёта, отсчёт с этого момента
    assert asyncio.run(activity.report_once(bot, log, state, admins, AT, "MSK", now=d1)) is None
    assert not bot.sent and state.load() == date(2026, 10, 21)

    log.day_file(date(2026, 10, 22)).write_text(
        '{"t": "12:00", "uid": 111, "name": "Ivan", "username": "", "url": "https://youtu.be/a", "platform": "YouTube"}\n')
    d2 = datetime(2026, 10, 23, 9, 1, tzinfo=TZ)
    assert asyncio.run(activity.report_once(bot, log, state, admins, AT, "MSK", now=d2)) == date(2026, 10, 22)
    assert [s[0] for s in bot.sent] == [1] and "youtu.be/a" in bot.sent[0][2]   # 333 не нажал /start
    assert state.load() == date(2026, 10, 22)
    # повторная проверка в тот же день — тишина
    assert asyncio.run(activity.report_once(bot, log, state, admins, AT, "MSK", now=d2)) is None


def test_report_not_marked_sent_when_nobody_got_it(tmp_path, log):
    state = activity.ReportState(tmp_path)
    state.save(date(2026, 10, 21))
    bot = FakeBot(fail={1: NetworkError("down")})
    now = datetime(2026, 10, 23, 9, 1, tzinfo=TZ)
    assert asyncio.run(activity.report_once(bot, log, state, [1], AT, "MSK", now=now)) is None
    assert state.load() == date(2026, 10, 21)      # повтор через час


def test_long_report_goes_as_file():
    rows = [{"t": "10:00", "uid": i, "name": f"u{i}", "username": "", "url": "https://youtu.be/" + "x" * 300,
             "platform": "YouTube"} for i in range(100)]
    r = activity.build_report(date(2026, 10, 22), rows, "UTC")
    assert len(r.messages) > activity.MAX_MESSAGES
    bot = FakeBot()
    asyncio.run(activity.send_report(bot, [1], r, date(2026, 10, 22)))
    kinds = [s[1] for s in bot.sent]
    assert kinds == ["msg", "doc"] and bot.sent[1][2] == "report-2026-10-22.txt"
    assert "Пользователей: 100" in bot.sent[0][2]


def test_parse_helpers():
    assert activity.parse_time("21:30") == dtime(21, 30)
    assert activity.parse_time("bad") == dtime(9, 0)
    assert str(activity.parse_tz("Nope/Zone")) == "UTC"


# ───────────── интеграция с обработчиками ─────────────

class FakeMsg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


def test_handlers_record_and_report_command(log, monkeypatch):
    from bot import handlers
    monkeypatch.setattr(handlers, "activity_log", log)
    monkeypatch.setattr(handlers, "ADMIN_IDS", [111])
    upd = NS(effective_user=IVAN, effective_chat=GROUP)
    handlers._record_activity(upd, ["https://vimeo.com/123", "https://example.com/x"])
    rows = log.load(log.today())
    assert [r["platform"] for r in rows] == ["Vimeo", ""] and rows[0]["chat"] == "Чат друзей"

    bot = FakeBot()
    msg = FakeMsg()
    asyncio.run(handlers.report_command(NS(effective_user=ANNA, message=msg), NS(bot=bot)))
    assert "restricted" in msg.replies[0] and not bot.sent
    asyncio.run(handlers.report_command(NS(effective_user=IVAN, message=msg), NS(bot=bot)))
    assert bot.sent and bot.sent[0][0] == 111 and "vimeo.com/123" in bot.sent[0][2]


def test_handlers_noop_when_disabled(monkeypatch):
    from bot import handlers
    monkeypatch.setattr(handlers, "activity_log", None)
    handlers._record_activity(NS(effective_user=IVAN, effective_chat=PRIVATE), ["https://youtu.be/a"])

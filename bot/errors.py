"""
Понятные пользователю описания ошибок.

Сырой текст yt-dlp («[instagram:story] 4002…: This content is unreachable. Use
--cookies-from-browser…») пользователю не показывается — только общее описание
причины. Оригинал пишется в лог (downloader), чтобы админ мог разобраться.
"""
import re

AUTH = "🔒 Доступ ограничен: публикация приватная или открывается только после входа в аккаунт."
AGE = "🔞 У публикации возрастное ограничение — без входа в аккаунт она недоступна."
BOT_CHECK = "🤖 Площадка попросила подтвердить, что запрос не от робота. Попробуйте позже."
RATE_LIMIT = "⏳ Площадка временно ограничила запросы. Попробуйте через несколько минут."
BLOCKED = "🚫 Площадка отклонила запрос. Попробуйте позже."
GEO = "🌍 Публикация недоступна в регионе, где работает бот."
UNSUPPORTED = "🤷 По этой ссылке не нашлось видео или фото."
NO_MEDIA = "🤷 В публикации нет видео или фото, которые можно скачать."
NETWORK = "📡 Площадка не отвечает. Попробуйте позже."
GONE = "🗑 Публикация не найдена — возможно, она удалена или ссылка неверна."
DOWNLOAD_FAILED = "Не удалось скачать файл. Попробуйте ещё раз или выберите другое качество."
GALLERY_FAILED = "Не удалось скачать ни одного фото или видео из публикации. Попробуйте позже."
INTERNAL = "⚠️ Внутренняя ошибка при обработке. Попробуйте ещё раз."
DEFAULT = ("Не удалось обработать ссылку: публикация может быть удалена, приватной, "
           "или площадка временно недоступна.")

# Порядок важен: первое совпадение побеждает
# (например, «Private video. Sign in…» — это AUTH, а не GONE).
_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), msg) for p, msg in (
    (r"not a bot|confirm you.?re not", BOT_CHECK),
    (r"confirm your age|age.?restrict|\b18\b|adult|inappropriate|comfortable for some audiences", AGE),
    # до AUTH: «geo-restricted» не должно попасть под «restricted»
    (r"your country|geo.?restrict|region|your location", GEO),
    (r"cookie|log ?in|sign ?in|logged.?in|registered users|unreachable|authenticat|private"
     r"|permission|follow(?:s|ing)? this account|members.?only|subscri|premium|restricted", AUTH),
    (r"\b429\b|too many requests|rate.?limit", RATE_LIMIT),
    (r"\b403\b|forbidden|ip address is blocked|blocked", BLOCKED),
    (r"unsupported url", UNSUPPORTED),
    (r"no video|no media|format is not available|no (?:video )?formats|empty media response", NO_MEDIA),
    (r"timed? ?out|connection|network is unreachable|name or service not known|name resolution"
     r"|\bssl\b|\beof\b|remote end closed|\b50[234]\b|bad gateway|service unavailable", NETWORK),
    (r"\b404\b|\b410\b|not found|unavailable|not available|removed|deleted|does not exist"
     r"|no longer|status code", GONE),
)]

# «ERROR: [instagram:story] 4002848070710841711: » — id может содержать «403»/«404»
_PREFIX_RE = re.compile(r"^\s*(?:ERROR:\s*)?(?:\[[^\]]+\]\s*(?:[^\s:]+:\s*)?)?")


def describe(error: BaseException | str) -> str:
    """Общее описание ошибки площадки без технических подробностей."""
    text = _PREFIX_RE.sub("", str(error), count=1)
    for rx, msg in _RULES:
        if rx.search(text):
            return msg
    return DEFAULT

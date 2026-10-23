# 🎬 Telegram Video Downloader Bot

Telegram-бот, который скачивает видео без водяных знаков из соцсетей через **yt-dlp**
и отправляет их с правильным соотношением сторон и превью. Фото-посты и карусели
Instagram и TikTok приходят альбомом.

## Supported Platforms

| Platform | Watermark-Free | Domains / что поддерживается |
|----------|:---:|---------|
| TikTok | ✅ | `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com`; видео и **фото-посты** (`/@user/photo/<id>`, слайд-шоу) |
| Instagram | ✅ | `instagram.com` (нужны cookies); видео, **фото, карусели**, сторис (фото и видео) |
| Facebook | ✅ | `facebook.com`, `fb.watch`, `fb.com` |
| Pinterest | ✅ | `pinterest.com`, `pin.it` |
| X (Twitter) | ✅ | `twitter.com`, `x.com` |
| YouTube | ✅ | `youtube.com`, `youtu.be`, `m.youtube.com` |
| Snapchat | ✅ | `snapchat.com`, `t.snapchat.com` |
| Twitch | ✅ | **клипы** `clips.twitch.tv/<slug>`, `twitch.tv/<channel>/clip/<slug>`, `m.twitch.tv/clip/<slug>` (в т.ч. вертикальная версия 9:16). Записи (`twitch.tv/videos/…`) не поддерживаются |
| VK | ✅ | видео и **клипы**: `vkvideo.ru/clip-…`, `vk.com/clip…`, `vk.com/clips…?z=clip…`, `vk.ru` |
| VK Video Live | ✅ | **клипы (моменты)** `live.vkvideo.ru/<channel>/clip/<id>`. Записи (`…/record/<id>`) не поддерживаются |
| RuTube | ✅ | `rutube.ru` |
| Kick | ✅ | **клипы** `kick.com/<channel>/clips/clip_…` и `kick.com/<channel>?clip=clip_…`. Записи (`/videos/…`) не поддерживаются |
| SoundCloud | — | треки, приватные по secret-ссылке, `on.soundcloud.com`, `m.soundcloud.com`; из сета — первый трек |
| Дзен | ✅ | видео `dzen.ru/video/watch/<id>` и **ролики** `dzen.ru/shorts/<id>`, `zen.yandex.ru`. Ссылки на каналы и статьи (`dzen.ru/<канал>`, `dzen.ru/a/…`) отклоняются |
| Одноклассники | ✅ | видео и клипы `ok.ru/video/<id>`, `m.ok.ru/…`, `ok.ru/dk?st.mvId=…`, `odnoklassniki.ru` |
| 9GAG | ✅ | видео-мемы и **фото-мемы** `9gag.com/gag/<id>` (фото приходит картинкой) |
| Imgur | ✅ | видео, GIF (`i.imgur.com/<id>.gifv`), одиночные **картинки**; из галереи/альбома — первый элемент |
| Tumblr | ✅ | видео из постов: `www.tumblr.com/<блог>/<id>`, `tumblr.com/<блог>/<id>`, `<блог>.tumblr.com/post/<id>` |
| Streamable | ✅ | `streamable.com/<id>` |
| Dailymotion | ✅ | `dailymotion.com/video/<id>`, `dai.ly/<id>` |
| Vimeo | ✅ | `vimeo.com/<id>`, приватные по ссылке с хешем `vimeo.com/<id>/<hash>`; видео только для вошедших — недоступны |

Прямые трансляции (канал в эфире) не скачиваются.
Записи эфиров Twitch, Kick и VK Video Live отключены — с этих площадок скачиваются только клипы.

Ссылки распознаются и без `https://` (`vk.ru/clip1_2`), в скрытых гиперссылках
и в подписях к пересланным медиа. На неподдерживаемую ссылку бот отвечает в личке.

### Twitch Clips

Штатный экстрактор yt-dlp `twitch:clips`. Поддерживаются все виды ссылок на клип:

* `https://clips.twitch.tv/<slug>`
* `https://www.twitch.tv/<channel>/clip/<slug>` (в т.ч. с `?filter=clips&range=…`)
* `https://m.twitch.tv/clip/<slug>`
* без `https://` и в скрытых гиперссылках

Особенности:

* **вертикальные клипы** — если у клипа есть вертикальная версия, Twitch отдаёт её
  отдельными форматами (`portrait-1080`, `portrait-720`…). Бот показывает обе версии
  разными кнопками: 🎬 16:9 и 📱 9:16. У вертикальных форматов Twitch поле `height` —
  короткая сторона (`portrait-720` = 720×1280), бот это учитывает в подписи «720p»;
* **размеры на кнопках приблизительные** (`≈`): клипы не отдают ни `filesize`, ни битрейт;
* **соотношение сторон** — из `aspect_ratio` метаданных, при отсутствии — `ffprobe` по потоку;
* ссылка на канал в эфире (`twitch.tv/<channel>`) отклоняется как прямая трансляция;
* **записи эфиров отключены** (слишком большие): `twitch.tv/videos/<id>`, `m.twitch.tv/videos/…`,
  `twitch.tv/<channel>/v/<id>`, `player.twitch.tv/?video=v…`, списки записей
  `twitch.tv/<channel>/videos` и коллекции — бот сразу отвечает 🚫 без сетевых запросов.
  Список отключённого — `_BLOCKED_EXTRACTORS` в `bot/downloader.py`.

### Дзен: ролики, каналы, статьи

Штатный экстрактор yt-dlp `ZenYandexIE` принимает только `/video/watch/…` и `/media/…`,
а ссылка на ролик `dzen.ru/shorts/<id>` попадает в экстрактор **канала** (`shorts` как имя
канала) — получилось бы случайное видео. Поэтому:

* `ZenYandexShortsIE` (`bot/extractors.py`) разбирает страницу ролика тем же кодом (JSON
  `ssrData`); если структура страницы другая — переходит на `dzen.ru/video/watch/<id>`
  (у ролика и видео общий id публикации);
* ссылки на каналы и статьи (`dzen.ru/<канал>`, `dzen.ru/a/…`, `dzen.ru/id/…`) отклоняются
  сразу, без сетевых запросов: «🚫 Это ссылка на канал или статью Дзена…»
  (`ZenYandexChannel` в `_BLOCKED_EXTRACTORS`).

### VK Video Live clips

yt-dlp из коробки не знает клипы VK Video Live, поэтому в `bot/vk_live.py` свой экстрактор.
Публичного API для клипов нет (`api.live.vkvideo.ru` / `api.vkplay.live` → 404),
но страница клипа рендерится на сервере и содержит JSON-состояние с `playerUrls`
(HLS на `okcdn.ru`):

1. ищется JSON с `playerUrls` в любом `<script>` (`window.X = {...}`, `application/json`,
   `JSON.parse("...")`), выбирается объект нашего клипа, а не соседних из рекомендаций;
2. fallback — прямые ссылки `.m3u8`/`.mp4` в HTML, метаданные из OpenGraph.

Записи эфиров (`…/record/<id>`, экстрактор yt-dlp `VKPlay`) отключены — бот сразу отвечает 🚫.

Ссылки на okcdn подписаны и живут ограниченное время — поэтому страница
запрашивается заново и при предпроверке, и при загрузке.

## Фото-посты и карусели (Instagram, TikTok, 9GAG, Imgur)

Если в посте есть хотя бы одно фото, бот не спрашивает качество, а сразу скачивает
весь пост и отправляет **одним альбомом** (подпись — у первого элемента):

```
🔍 Анализирую ссылку...
⏳ Instagram · 🖼 4 фото · 🎬 1 видео
📥 Скачиваю...
[альбом: 4 фото + 1 видео]
Подпись поста
👤 Автор
📱 Instagram · 🖼 4 фото · 🎬 1 видео
```

| Что | Как |
|---|---|
| Instagram: фото-пост, карусель (фото + видео) | штатный экстрактор yt-dlp: фото — элементы без видеоформатов, картинка берётся из `image_versions2` (самая большая) |
| Instagram: фото-сторис | `InstagramStoryMediaIE` (`bot/extractors.py`) — штатный экстрактор сторис фото выбрасывает |
| TikTok: фото-пост / слайд-шоу | `TikTokMediaIE` — принимает ссылки `/photo/`, достаёт `imagePost.images` из того же ответа, без лишних запросов |
| 9GAG: фото-мем | штатный экстрактор: фото-пост без видеоформатов, картинка — самая большая из `images` (700 px) |
| Imgur: одиночная картинка | штатный экстрактор: картинка из превью страницы (`twitter:image` / `og:image`) |
| Пост только из видео | как раньше — кнопки выбора качества |

* Telegram кладёт в альбом **до 10** элементов — больше уходит несколькими альбомами подряд
  (TikTok-слайд-шоу бывает до 35 фото). Максимум элементов из поста — `GALLERY_MAX_ITEMS`,
  при обрезке в подписи пишется «Показаны первые N из M»;
* фото JPEG/PNG уходят как есть; WebP/HEIC/AVIF, файлы больше 10 MB и кадры больше
  лимитов Telegram (ширина+высота ≤ 10000) конвертируются ffmpeg в JPEG ≤ 2560 px;
* если Telegram не принял альбом как фото (размеры, формат) — бот отправляет его документами;
* элемент, который не скачался, не роняет весь пост — в подписи «Не удалось загрузить: N»;
* **музыка слайд-шоу TikTok** — в MP3 (`SLIDESHOW_AUDIO`):
  * `button` (по умолчанию) — после альбома сообщение «🎵 Музыка из слайд-шоу» с кнопкой
    `🎵 Скачать музыку · MP3 · ≈312 KB`; по нажатию — обычный MP3-поток бота (очередь, обложка, теги);
  * `auto` — MP3 приходит сразу после альбома;
  * `off` — не предлагать.

  В MP3 и в Telegram-плеере — название трека и исполнитель (а не подпись поста).
  Если исходник уже MP3, он не перекодируется; M4A → MP3 192 kbps.

Проверка без Telegram: `python -m bot.debug "<url>"` покажет элементы поста,
`-d 0` — скачает весь пост и покажет, во что превратится каждое фото.

## Ежедневный отчёт админам

Опция `DAILY_REPORT=1`: раз в сутки в `DAILY_REPORT_TIME` (часовой пояс `DAILY_REPORT_TZ`)
каждый из `ADMIN_IDS` получает в личку отчёт за прошедший день — кто пользовался ботом
и какие ссылки отправлял (в том числе неподдерживаемые):

```
📊 Отчёт за 22.10.2026 (Europe/Moscow)
Пользователей: 2 · ссылок: 3

👤 Иван Петров (@ivan) · 123456789 — 2
10:15 TikTok
https://www.tiktok.com/@user/video/7412…
18:40 YouTube · Чат друзей
https://youtu.be/abc

👤 Anna · 987654321 — 1
12:03 не поддерживается
https://example.com/page
```

* **важно:** каждый админ должен хотя бы раз написать боту `/start` — первым бот писать не может;
* `/report` — отчёт за сегодня на текущий момент (только админам);
* в группах рядом со ссылкой — название чата; самые активные пользователи — сверху;
* длинный отчёт (больше 5 сообщений) приходит сводкой + файлом `report-ГГГГ-ММ-ДД.txt`;
* бот был выключен в момент отправки — отчёт уйдёт сразу после старта; если ни одному
  админу не удалось доставить (сеть, не нажат `/start`) — повтор раз в час;
* после простоя отправляется только отчёт за последний день, без «хвоста» за неделю;
* в день первого включения отчёта нет — отсчёт начинается с момента запуска.

Хранение: журнал `./data/activity/ГГГГ-ММ-ДД.jsonl` (по строке на ссылку, ~250 байт),
не больше **5 MB в день** — дальше ссылки только считаются («+N не записано»);
хранятся файлы за сегодня и вчера, старше — удаляются. Дата последней отправки —
`./data/report_state.json`. Журнал содержит ID, имена и ссылки пользователей — держите
`./data` закрытой (`chmod 700`).

## Ошибки

Пользователь видит только общее описание причины, без вывода yt-dlp и технических деталей:

| Причина (по тексту ошибки площадки) | Сообщение |
|---|---|
| приватно, нужен вход, cookies, «content is unreachable» | 🔒 Доступ ограничен: публикация приватная или открывается только после входа в аккаунт |
| возрастное ограничение | 🔞 … без входа в аккаунт она недоступна |
| проверка «не робот» | 🤖 Площадка попросила подтвердить, что запрос не от робота |
| 429 / rate limit | ⏳ Площадка временно ограничила запросы |
| 403 / IP заблокирован | 🚫 Площадка отклонила запрос |
| гео-блок | 🌍 Публикация недоступна в регионе, где работает бот |
| 404 / удалено | 🗑 Публикация не найдена |
| таймауты, сеть | 📡 Площадка не отвечает |
| остальное | Не удалось обработать ссылку: публикация может быть удалена, приватной… |

Правила — `bot/errors.py`. Полный текст ошибки yt-dlp пишется в лог
(`yt-dlp error for <url>: …`), так что разобраться можно через `docker compose logs bot`.

## Выбор качества

После анализа ссылки бот определяет соотношение сторон исходника и показывает
кнопки с разрешением, соотношением и размером каждого варианта:

```
🎯 YouTube
Название видео
⏱ 0:45
📐 9:16 · вертикальное · 1080×1920

[ 🎬 1080p · 9:16 · 15.2 MB ]
[ 🎬 720p · 9:16 · 7.5 MB   ]
[ 🎬 480p · 9:16 · ≈5.1 MB  ]
[ 🎵 MP3 · ≈1.0 MB ]
[ ✖️ Отмена ]
```

* от 5 вариантов кнопки раскладываются в 2 столбца с компактными подписями
  (соотношение сторон — в шапке, ориентация — иконкой 🎬/📱); группы
  (горизонтальные / вертикальные / аудио) в одной строке не смешиваются.
  Порог — `TWO_COLUMNS_FROM` в `bot/handlers.py`:

  ```
  📐 16:9 · горизонтальное · 3840×2160
  [ ⚠️ 🎬 2160p · 1.31 GB ] [ 🎬 1440p · 620 MB ]
  [ 🎬 1080p60 · 324 MB   ] [ 🎬 720p · 124 MB  ]
  [ 🎬 480p · ≈81 MB      ] [ 🎬 360p · ≈52 MB  ]
  [ 🎵 MP3 · ≈14 MB ]
  [ ✖️ Отмена ]
  ```
* соотношение сторон — из метаданных yt-dlp (`width`/`height`/`aspect_ratio`);
  если площадка их не отдаёт (Twitch-клипы, часть HLS) — `ffprobe` по ссылке на лучший
  поток (читаются только заголовки, ~1 с); учитывается поворот и то, что у Twitch
  `height` вертикального клипа — короткая сторона;
* «1080p» — всегда короткая сторона кадра: вертикальный 1080×1920 — это 1080p, не 1920p;
* если площадка отдаёт несколько версий кадра (Twitch-клипы: обычная 16:9 + вертикальная 9:16),
  каждая идёт отдельной кнопкой: 🎬 горизонтальные, затем 📱 вертикальные;
  fallback-селектор сохраняет ориентацию (`[aspect_ratio<?1]` / `[aspect_ratio>?1]`):

  ```
  📐 Есть версии: 16:9 (горизонтальное) и 9:16 (вертикальное)
  [ 🎬 1080p60 · 16:9 · ≈23.2 MB ]
  [ 🎬 720p60 · 16:9 · ≈15.4 MB  ]
  [ 📱 1080p60 · 9:16 · ≈23.2 MB ]
  [ 📱 720p60 · 9:16 · ≈15.4 MB  ]
  [ 🎵 MP3 · ≈703.1 KB ]
  ```
* `≈` — размер оценён по битрейту; ⚠️ — больше `WARNING_THRESHOLD_MB`;
* варианты больше `MAX_FILE_SIZE_MB` скрываются (в сообщении видно, сколько);
* на каждое разрешение — лучший формат, с приоритетом H.264 (`PREFER_H264`);
* для аудио-источников (SoundCloud) — только аудио-варианты:
  `🎵 MP3 128 kbps` (без перекодирования, если исходник уже MP3),
  `🎧 AAC 160 kbps (M4A)`, `💾 Оригинал` (WAV/FLAC уходят документом);
  превью Go+ (30 с) не предлагаются, если есть полная версия;
* к аудио добавляются теги (title/artist) и обложка; к видео в подписи — 📐 соотношение и размер кадра;
* селектор с fallback'ами: если ссылки форматов успели протухнуть, берётся
  ближайшее разрешение не выше выбранного.

## Как бот готовит видео для Telegram

Без этого Telegram показывает «чёрный квадрат»:
* выбор формата с приоритетом H.264 + AAC (`PREFER_H264`);
* `ffprobe` → `width`/`height` с учётом поворота, `duration`;
* faststart (moov в начале), при необходимости remux;
* VP9/AV1/HEVC → транскод в H.264 (до `TRANSCODE_MAX_MB`);
* превью JPEG ≤320 px, <200 KB.

## Cookies

*Instagram* — обязательно, cookies залогиненного пользователя Firefox:

```bash
yt-dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.instagram.com/p/ID/"
sudo chown 1000:1000 cookies.txt && chmod 600 cookies.txt   # 1000 = APP_UID из .env
```

Файл должен принадлежать пользователю бота (`APP_UID`, по умолчанию 1000), иначе при
`chmod 600` бот его не прочитает и в логе будет `COOKIES_FILE=… не читается ботом`.

*VK* — опционально (приватные/18+ видео или если VK требует вход). Аналогично через
`https://vk.com/`, файл `vk_cookies.txt`, раскомментировать строку в `docker-compose.yml`
и указать `VK_COOKIES_FILE=/app/vk_cookies.txt`.

Боту всегда передаётся временная копия cookies, оригинал монтируется read-only.

Фото-сторис, приватные посты и большинство каруселей Instagram без cookies недоступны —
пользователь увидит «🔒 Доступ ограничен». Если так отвечают и публичные посты, cookies устарели:
выгрузите их заново.

> Если файла `cookies.txt` на хосте нет, Docker создаст вместо него **директорию**.
> Создайте файл заранее: `touch cookies.txt`.

## Сеть: vk.com недоступен

VK-экстрактор yt-dlp всегда ходит в API на `https://vk.com/al_video.php`,
даже для ссылок `vk.ru` / `vkvideo.ru`. Если в логе `Connection to vk.com timed out`:

| Ситуация | Настройка |
|---|---|
| IPv6 не работает, IPv4 работает | `FORCE_IPV4=1` |
| vk.com недоступен, vk.ru доступен | `VK_API_HOST=vk.ru` |
| VK недоступен вовсе | `VK_PROXY=socks5://host:port` (или `PROXY` для всех платформ) |

Cookies VK выгружаются для того домена, куда реально идут запросы
(при `VK_API_HOST=vk.ru` — с `https://vk.ru/`).

## Отладка ссылки

```bash
docker compose exec bot python -m bot.debug "<url>"             # форматы площадки и варианты бота
docker compose exec bot python -m bot.debug "<url>" -d 0         # + скачать вариант [0], проверить дорожки
docker compose exec bot python -m bot.debug "<url>" -d 0 --keep  # не удалять файл
docker compose exec bot python -m bot.debug "<url>" --json       # сырые форматы yt-dlp
```

Показывает таблицу форматов (⚠ — у видеоформата неизвестен `acodec`), варианты бота
с селекторами, а с `-d` — потоки файла после yt-dlp и после `prepare_video` (✅/❌ звук).

**Видео без звука.** После каждой загрузки бот проверяет аудиодорожки (`ffprobe`) и пишет
в лог реально скачанные форматы:
`Downloaded x.mp4: 2000[v=None,a=None,m3u8_native] | audio streams: 0`.
Если звука нет, а у источника есть отдельные аудиоформаты (типично для X: HLS без `CODECS`,
yt-dlp считает видеопоток «полным»), бот докачивает `bestaudio` и муксит его
(видео копией, звук → AAC). Если отдельного аудио нет — видео без звука в оригинале.

## Docker

```bash
mkdir -p downloads data tg-api-data && touch cookies.txt
sudo chown -R 1000:1000 downloads data cookies.txt && chmod 600 cookies.txt && chmod 700 data   # 1000 = APP_UID
docker compose up -d --build     # сборка и запуск
docker compose logs -f           # логи
docker compose down              # остановка
```

Архитектура контейнеров:
* `telegram-api-server` — локальный Bot API в режиме `--local` (`TELEGRAM_LOCAL=1`), лимит 2000 MB;
  данные в `./tg-api-data`, `./downloads` смонтирован read-only по тому же пути `/app/downloads`;
* `bot` — передаёт серверу путь к файлу (`file:///app/downloads/...`) вместо HTTP-загрузки.

yt-dlp для YouTube требует JS-runtime — в образ добавлен Deno.
Kick стоит за Cloudflare: экстрактор yt-dlp использует impersonate браузера, для этого
нужен `curl_cffi` (`yt-dlp[default,curl-cffi]` в `requirements.txt`). Свои HTTP-заголовки
бот для Kick не подставляет — иначе User-Agent не совпадёт с TLS-отпечатком и будет 403.
Проверка: `python -m bot.debug <url>` показывает, установлен ли `curl_cffi`.
Extractor'ы часто ломаются: пересобирайте образ регулярно (`docker compose build --no-cache bot`).

### Запуск не от root

Бот в контейнере работает как пользователь `app` с UID/GID из `.env`
(`APP_UID` / `APP_GID`, по умолчанию `1000:1000`). Ему нужны:

| Путь на хосте | Права |
|---|---|
| `./downloads` | владелец `APP_UID`, `755` — бот пишет, `telegram-api-server` читает (read-only) |
| `./data` | владелец `APP_UID`, `700` — журнал для ежедневного отчёта |
| `cookies.txt`, `vk_cookies.txt` | владелец `APP_UID`, `600` |
| `./tg-api-data` | не трогать — это данные другого контейнера |

При старте бот проверяет права: нет записи в `downloads` — в логе `CRITICAL Нет прав на запись…`
с готовой командой `chown`, контейнер завершается; cookies не читаются — `ERROR … не читается ботом`,
бот работает дальше без cookies.

#### Переезд с версии, где бот работал от root

```bash
cd /path/to/bot
docker compose down

# 1. Выбрать UID/GID. По умолчанию 1000:1000. Можно отдельный, не совпадающий
#    ни с одним пользователем хоста, например 10001 — тогда указать его и ниже.
grep -q '^APP_UID=' .env || printf 'APP_UID=1000\nAPP_GID=1000\n' >> .env

# 2. Передать ему файлы, которые раньше принадлежали root
mkdir -p data
sudo chown -R 1000:1000 downloads data
sudo chmod 755 downloads && sudo chmod 700 data
sudo chown 1000:1000 cookies.txt && sudo chmod 600 cookies.txt
[ -f vk_cookies.txt ] && sudo chown 1000:1000 vk_cookies.txt && sudo chmod 600 vk_cookies.txt

# 3. Пересобрать (UID зашивается в образ) и запустить
docker compose build --no-cache bot
docker compose up -d

# 4. Проверить
docker compose exec bot id                     # uid=1000(app) gid=1000(app)
docker compose logs bot | grep -iE "прав|не читается|uid="
docker compose ps                              # bot — healthy через ~1–2 мин
```

Если `cookies.txt` на хосте оказался **папкой** (Docker создал её, когда файла не было):
`docker compose down && sudo rm -r cookies.txt && touch cookies.txt`, затем шаг 2.

Откат: вернуть прежний `Dockerfile.python` и `docker compose up -d --build` —
права `1000:1000` root'у не мешают, менять обратно ничего не нужно.

### Ресурсы, логи, healthcheck

| | Значение |
|---|---|
| RAM | `mem_limit: 512m` на каждый контейнер. Бот в простое ~100–150 MB; пики — транскод VP9/AV1/HEVC → H.264 (`TRANSCODE_NON_H264`) |
| Логи | Docker `json-file`, `max-size: 5m`, `max-file: 3` → не больше 15 MB на сервис |
| Healthcheck | бот раз в 30 с обновляет `/tmp/bot.heartbeat`; старше 120 с — контейнер `unhealthy` |
| Перезапуск | `restart: unless-stopped` — после падения и перезагрузки VPS |

Фото-посты почти не добавляют нагрузки: картинки пишутся на диск потоком,
конвертация одного фото ffmpeg — до ~100 MB на секунду-две.

> Docker не перезапускает `unhealthy`-контейнер сам (это делает только Swarm) —
> статус виден в `docker ps` и пригоден для внешнего мониторинга.

## Тесты

```bash
pip install -r requirements.txt pytest
BOT_TOKEN=x DOWNLOAD_DIR=/tmp/dl python -m pytest -q tests
```

Ответы Instagram/TikTok подменены фикстурами, но разбираются штатными экстракторами
yt-dlp и подклассами бота; загрузка идёт с локального HTTP-сервера, конвертация — ffmpeg.
Нужен `ffmpeg` в системе. В Docker-образ тесты не попадают.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick intro |
| `/help` | Supported platforms and usage guide |
| `/id` | Check your TG ID |
| `/status` | Active downloads & waiting queue |
| `/stats` | Global download statistics (admins only) |
| `/report` | Activity report for today so far (admins only, needs `DAILY_REPORT=1`) |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | — | Telegram Bot API token (required) |
| `BOT_API_URL` | — | URL локального Bot API (в compose задан) |
| `TELEGRAM_LOCAL_MODE` | `1` при `BOT_API_URL` | Отправка файлов по локальному пути |
| `MAX_FILE_SIZE_MB` | `2000` / `50` | Лимит (локальный / облачный Bot API) |
| `WARNING_THRESHOLD_MB` | `1024` | Варианты больше порога помечаются ⚠️ |
| `DISK_RESERVE_MB` | `100` | Минимум свободного места |
| `DOWNLOAD_DIR` | `./downloads` | Временная папка |
| `ADMIN_IDS` | — | ID админов для `/stats` |
| `COOKIES_FILE` | — | Cookies Instagram |
| `VK_COOKIES_FILE` | — | Cookies VK (опционально) |
| `PREFER_H264` | `1` | Предпочитать H.264 (YouTube 4K придёт в 1080p) |
| `TRANSCODE_NON_H264` | `1` | Транскодировать VP9/AV1/HEVC в H.264 |
| `TRANSCODE_MAX_MB` | `300` | Максимальный размер для транскода |
| `COOLDOWN_SECONDS` | `5` | Кулдаун на пользователя |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Параллельных загрузок всего |
| `PENDING_URL_TTL` | `3600` | Время жизни кнопок выбора качества, сек |
| `APP_UID` / `APP_GID` | `1000` | Пользователь бота в контейнере (при сборке образа) |
| `DAILY_REPORT` | `0` | Ежедневный отчёт админам: кто пользовался ботом и какие ссылки отправлял |
| `DAILY_REPORT_TIME` | `09:00` | Время отправки отчёта, ЧЧ:ММ |
| `DAILY_REPORT_TZ` | `UTC` | Часовой пояс отчёта и границы суток, напр. `Europe/Moscow` |
| `DATA_DIR` | `./data` | Журнал для отчёта (в compose — volume `./data`) |
| `GALLERY_MAX_ITEMS` | `35` | Максимум фото/видео из одного поста Instagram/TikTok (альбомы по 10) |
| `SLIDESHOW_AUDIO` | `button` | Музыка слайд-шоу TikTok в MP3: `button` / `auto` / `off` |
| `VK_API_HOST` | — | Хост вместо vk.com для API VK (напр. `vk.ru`) |
| `PROXY` / `VK_PROXY` | — | Прокси yt-dlp для всех / только для VK |
| `FORCE_IPV4` | `0` | Только IPv4 |
| `SOCKET_TIMEOUT` | `30` | Таймаут сокета yt-dlp, сек |

## Architecture

```
bot/
├── main.py           # Entry point, PTB Application (concurrent updates, local mode)
├── config.py         # Environment-based configuration
├── handlers.py       # Telegram command, message & callback handlers
├── downloader.py     # yt-dlp wrapper: formats, cookies, size checks, cleanup
├── formats.py        # варианты качества с размерами для кнопок
├── vk_live.py        # yt-dlp extractor: VK Video Live clips (моменты)
├── extractors.py     # подклассы yt-dlp: фото TikTok (/photo/, слайд-шоу), фото-сторис Instagram, ролики Дзена
├── gallery.py        # фото-посты/карусели: разбор ответа yt-dlp в список элементов альбома
├── errors.py         # понятные описания ошибок вместо вывода yt-dlp
├── activity.py       # журнал ссылок и ежедневный отчёт админам (DAILY_REPORT)
├── ytdlp_patches.py  # runtime-патчи yt-dlp (VK_API_HOST)
├── debug.py          # CLI-диагностика ссылки: python -m bot.debug <url>
├── media.py          # ffprobe, faststart/H.264, probe_url (размеры кадра), превью, подготовка фото
├── queue_manager.py  # Global / per-user download slots
├── stats.py          # In-memory statistics
└── utils.py          # URL extraction/normalization, platform detection
```
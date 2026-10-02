# 🎬 Telegram Video Downloader Bot

Telegram-бот, который скачивает видео без водяных знаков из соцсетей через **yt-dlp**
и отправляет их с правильным соотношением сторон и превью.

## Supported Platforms

| Platform | Watermark-Free | Domains / что поддерживается |
|----------|:---:|---------|
| TikTok | ✅ | `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com` |
| Instagram | ✅ | `instagram.com` (нужны cookies) |
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
chmod 600 cookies.txt
```

*VK* — опционально (приватные/18+ видео или если VK требует вход). Аналогично через
`https://vk.com/`, файл `vk_cookies.txt`, раскомментировать строку в `docker-compose.yml`
и указать `VK_COOKIES_FILE=/app/vk_cookies.txt`.

Боту всегда передаётся временная копия cookies, оригинал монтируется read-only.

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
mkdir -p downloads tg-api-data && touch cookies.txt
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

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick intro |
| `/help` | Supported platforms and usage guide |
| `/id` | Check your TG ID |
| `/status` | Active downloads & waiting queue |
| `/stats` | Global download statistics (admins only) |

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
├── ytdlp_patches.py  # runtime-патчи yt-dlp (VK_API_HOST)
├── debug.py          # CLI-диагностика ссылки: python -m bot.debug <url>
├── media.py          # ffprobe, faststart/H.264, probe_url (размеры кадра), превью
├── queue_manager.py  # Global / per-user download slots
├── stats.py          # In-memory statistics
└── utils.py          # URL extraction/normalization, platform detection
```
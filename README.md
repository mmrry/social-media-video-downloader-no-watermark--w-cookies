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
| Twitch | ✅ | `twitch.tv`, `clips.twitch.tv`, `m.twitch.tv` |
| VK | ✅ | видео и **клипы**: `vkvideo.ru/clip-…`, `vk.com/clip…`, `vk.com/clips…?z=clip…`, `vk.ru` |
| VK Video Live | ✅ | **клипы (моменты)** `live.vkvideo.ru/<channel>/clip/<id>`, записи `…/record/<id>` |
| RuTube | ✅ | `rutube.ru` |

Прямые трансляции (канал в эфире) не скачиваются — только записи и клипы.

Ссылки распознаются и без `https://` (`vk.ru/clip1_2`), в скрытых гиперссылках
и в подписях к пересланным медиа. На неподдерживаемую ссылку бот отвечает в личке.

### VK Video Live clips

yt-dlp из коробки не знает клипы VK Video Live, поэтому в `bot/vk_live.py` свой экстрактор.
Публичного API для клипов нет (`api.live.vkvideo.ru` / `api.vkplay.live` → 404),
но страница клипа рендерится на сервере и содержит JSON-состояние с `playerUrls`
(HLS на `okcdn.ru`):

1. ищется JSON с `playerUrls` в любом `<script>` (`window.X = {...}`, `application/json`,
   `JSON.parse("...")`), выбирается объект нашего клипа, а не соседних из рекомендаций;
2. fallback — прямые ссылки `.m3u8`/`.mp4` в HTML, метаданные из OpenGraph.

Ссылки на okcdn подписаны и живут ограниченное время — поэтому страница
запрашивается заново и при предпроверке, и при загрузке.

## Выбор качества

После анализа ссылки бот показывает кнопки со всеми доступными разрешениями
и размером каждого варианта, плюс MP3 и «Отмена»:

```
[ 🎬 1080p60 · 324.0 MB ] [ 🎬 720p · 123.7 MB ]
[ 🎬 480p · ≈80.8 MB    ] [ 🎬 360p · ≈42.9 MB  ]
[ 🎵 MP3 · ≈13.7 MB ]
[ ✖️ Отмена ]
```

* `≈` — размер оценён по битрейту (площадка не отдала точный);
* ⚠️ — больше `WARNING_THRESHOLD_MB`;
* варианты больше `MAX_FILE_SIZE_MB` скрываются (в сообщении видно, сколько);
* на каждое разрешение — лучший формат, с приоритетом H.264 (`PREFER_H264`),
  чтобы в Telegram было превью без транскода;
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
├── media.py          # ffprobe, faststart/H.264, thumbnails for Telegram
├── queue_manager.py  # Global / per-user download slots
├── stats.py          # In-memory statistics
└── utils.py          # URL extraction/normalization, platform detection
```

## TODO

* kick.com/<user>/clips/
* Instagram-карусели: сейчас скачивается только первый элемент
* Персистентная статистика (json/sqlite: ссылка, UID, статус)
* VK stories (нужен логин)

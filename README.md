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
| `WARNING_THRESHOLD_MB` | `1024` | Порог подтверждения для больших файлов |
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
| `PENDING_URL_TTL` | `3600` | Время жизни кнопок выбора формата, сек |

## Architecture

```
bot/
├── main.py           # Entry point, PTB Application (concurrent updates, local mode)
├── config.py         # Environment-based configuration
├── handlers.py       # Telegram command, message & callback handlers
├── downloader.py     # yt-dlp wrapper: formats, cookies, size checks, cleanup
├── vk_live.py        # yt-dlp extractor: VK Video Live clips (моменты)
├── media.py          # ffprobe, faststart/H.264, thumbnails for Telegram
├── queue_manager.py  # Global / per-user download slots
├── stats.py          # In-memory statistics
└── utils.py          # URL extraction/normalization, platform detection
```

## TODO

* Выбор качества перед загрузкой
* kick.com/<user>/clips/
* Instagram-карусели: сейчас скачивается только первый элемент
* Персистентная статистика (json/sqlite: ссылка, UID, статус)
* VK stories (нужен логин)

# 🎬 Telegram Video Downloader Bot

A Telegram bot that downloads high-quality, watermark-free videos from popular social media platforms using **yt-dlp**.

## Supported Platforms

| Platform | Watermark-Free | Domains |
|----------|:---:|---------|
| TikTok | ✅ | `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com"` |
| Instagram | ✅ | `instagram.com` | 
| Facebook | ✅ | `facebook.com`, `fb.watch`, `fb.com` |
| Pinterest | ✅ | `pinterest.com`, `pin.it` |
| X (Twitter) | ✅ | `twitter.com`, `x.com` |
| Youtube | ✅ | `youtube.com`, `youtu.be`, `m.youtube.com` |
| Snapchat | ✅ | `snapchat.com`, `t.snapchat.com` |
| Twitch | ✅ | `"twitch.tv", "clips.twitch.tv", "m.twitch.tv` |

### TODO: download VK story (? need login) 

*FOR Instagram* - need COOKIES.txt; from Firefox login Instagram user

`yt-dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.instagram.com/p/ID/"`

`chown -R $USER:$USER ./downloads/`

`chmod 777 downloads/`

`chmod 666 cookies.txt` 

## Docker

`docker compose up -d --build`

To check logs: `docker compose logs -f`

## Usage

1. Open your bot in Telegram
2. Send `/start` to see the welcome message
3. Paste any supported video URL
4. The bot will download and send the video back in best quality!

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick intro |
| `/help` | Supported platforms and usage guide |
| `/id` | Check yours TG ID |
| `/status` | Check bot load & queue |
| `/stats` | Global download statistics only for Admin ID |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | — | Your Telegram Bot API token (required) |
| `MAX_FILE_SIZE_MB` | `50` | Max file size for uploads (Telegram limit) |
| `DOWNLOAD_DIR` | `./downloads` | Temp directory for video files |
| `ADMIN_IDS` |  `/stats` | User Admins id for `/stats` command |
| `COOKIES_FILE` | `/app/cookies.txt` | Docker path cookies files. Local cookies.txt at root bot path |
| `COOLDOWN_SECONDS` | `5` | Cooldown secs |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Maximum parallel downloads |

## Architecture

```
bot/
├── main.py         # Entry point, bot initialization
├── config.py       # Environment-based configuration
├── handlers.py     # Telegram command & message handlers
├── downloader.py   # yt-dlp wrapper with quality optimization
└── utils.py        # URL detection, platform identification
```
# social-media-video-downloader-no-watermark--w-cookies

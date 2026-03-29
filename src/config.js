import "dotenv/config";
import { existsSync, mkdirSync } from "fs";

// ── Bot ──────────────────────────────────────────────────────────
export const BOT_TOKEN = process.env.BOT_TOKEN || "";
if (!BOT_TOKEN) throw new Error("BOT_TOKEN is missing from .env");

// Admin user IDs (comma-separated in .env, e.g. "123456,789012")
export const ADMIN_IDS = (process.env.ADMIN_IDS || "")
  .split(",")
  .map((s) => parseInt(s.trim(), 10))
  .filter(Number.isFinite);

// Path to a Netscape-format cookies file (for Instagram etc.)
export const COOKIES_FILE = process.env.COOKIES_FILE || "";

// Жёсткий лимит стандартного Telegram cloud API
export const TELEGRAM_CLOUD_LIMIT_MB    = 50;
export const TELEGRAM_CLOUD_LIMIT_BYTES = TELEGRAM_CLOUD_LIMIT_MB * 1024 * 1024;

// Лимит для больших файлов через local API (по умолчанию 500 МБ)
export const MAX_LARGE_FILE_SIZE_MB    = parseInt(process.env.MAX_LARGE_FILE_SIZE_MB || "1000", 10);
export const MAX_LARGE_FILE_SIZE_BYTES = MAX_LARGE_FILE_SIZE_MB * 1024 * 1024;

// Download dir
export const DOWNLOAD_DIR = process.env.DOWNLOAD_DIR || "./downloads";
if (!existsSync(DOWNLOAD_DIR)) mkdirSync(DOWNLOAD_DIR, { recursive: true });

export const COOLDOWN_SECONDS = parseInt(process.env.COOLDOWN_SECONDS || "5", 10);
export const MAX_CONCURRENT = parseInt(process.env.MAX_CONCURRENT_DOWNLOADS || "3", 10);

// Адрес local Bot API сервера — прокидывается из docker-compose автоматически
export const LOCAL_BOT_API_URL = process.env.LOCAL_BOT_API_URL || "";


// ── Platforms ────────────────────────────────────────────────────
export const PLATFORMS = {
  TikTok:      ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
  Instagram:   ["instagram.com"],
  Facebook:    ["facebook.com", "fb.watch", "fb.com"],
  Pinterest:   ["pinterest.com", "pin.it"],
  "X (Twitter)": ["twitter.com", "x.com"],
  YouTube:     ["youtube.com", "youtu.be", "m.youtube.com"],
  Reddit:      ["reddit.com", "redd.it", "v.redd.it"],
  Snapchat:    ["snapchat.com", "t.snapchat.com"],
  Threads:     ["threads.net"],
  Twitch:      ["twitch.tv", "clips.twitch.tv", "m.twitch.tv"],
};

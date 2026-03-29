import { createReadStream } from "fs";
import { Telegraf } from "telegraf";
import { message } from "telegraf/filters";

import {
  BOT_TOKEN, ADMIN_IDS, COOLDOWN_SECONDS, PLATFORMS,
  PLATFORMS_NO_SIZE_LIMIT, LOCAL_BOT_API_URL,
} from "./config.js";
import { extractUrls, identifyPlatform, formatBytes, escapeHtml } from "./utils.js";
import { download, cleanup, DownloadError, FileTooLargeError } from "./downloader.js";
import { stats } from "./stats.js";
import { queue } from "./queue.js";

if (!LOCAL_BOT_API_URL) {
  console.warn("⚠️ LOCAL_BOT_API_URL not set — files >50 MB will fail.");
}

const bot = new Telegraf(BOT_TOKEN, {
  telegram: {
    // Все запросы идут через local Bot API (http://tg-bot-api:8081).
    // Файлы ≤50 МБ — сервер проксирует в Telegram как обычно.
    // Файлы >50 МБ — сервер принимает стрим и загружает сам без лимита.
    ...(LOCAL_BOT_API_URL ? { apiRoot: LOCAL_BOT_API_URL } : {}),
    // Увеличиваем таймаут: стандартные 30 с не хватит для стрима 200+ МБ
    timeoutMs: 20 * 60 * 1000,
  },
});

if (LOCAL_BOT_API_URL) {
  console.log(`🔗 Local Bot API: ${LOCAL_BOT_API_URL}`);
}

// ── Cooldown ─────────────────────────────────────────────────────
const lastRequest = new Map();
function checkCooldown(userId) {
  const now = Date.now();
  const diff = (now - (lastRequest.get(userId) || 0)) / 1000;
  if (diff < COOLDOWN_SECONDS) return Math.ceil(COOLDOWN_SECONDS - diff);
  lastRequest.set(userId, now);
  return 0;
}

// ── /start ───────────────────────────────────────────────────────
bot.start((ctx) => {
  stats.recordUser(ctx.from.id);
  return ctx.replyWithHTML(
    `👋 <b>Welcome to the Video Downloader Bot!</b>\n\n` +
    `I download the best quality video from:\n` +
    `<i>${Object.keys(PLATFORMS).join(", ")}</i>\n\n` +
    `⚡ Just send me a link!`
  );
});

// ── /help ────────────────────────────────────────────────────────
bot.help((ctx) =>
  ctx.replyWithHTML(
    [
      "📖 <b>How to use</b>\n",
      "Simply paste a video link — the bot downloads and sends it automatically.\n",
      "<b>Supported Platforms:</b>",
      ...Object.keys(PLATFORMS).sort().map((p) => `• ${p}`),
      "\n<b>Commands:</b>",
      "/id — Your Telegram user ID",
      "/status — Queue status",
      "/stats — Statistics (admin)",
    ].join("\n")
  )
);

// ── /id ──────────────────────────────────────────────────────────
bot.command("id", (ctx) =>
  ctx.reply(`Your Telegram ID is: ${ctx.from.id}`)
);

// ── /status ──────────────────────────────────────────────────────
bot.command("status", (ctx) =>
  ctx.replyWithHTML(
    `🛰 <b>Bot Status</b>\n\n` +
    `Active downloads: <b>${queue.activeDownloads()}</b>\n` +
    `Waiting in queue: <b>${queue.queueDepth()}</b>`
  )
);

// ── /stats ───────────────────────────────────────────────────────
bot.command("stats", (ctx) => {
  if (!ADMIN_IDS.includes(ctx.from.id))
    return ctx.reply("🔒 Admin only.");
  return ctx.replyWithHTML(stats.summary());
});

// ── URL handler ──────────────────────────────────────────────────
bot.on(message("text"), async (ctx) => {
  const text = ctx.message.text.trim();
  if (text.startsWith("/")) return;

  stats.recordUser(ctx.from.id);

  const urls = extractUrls(text);
  if (!urls.length) return;

  const wait = checkCooldown(ctx.from.id);
  if (wait > 0)
    return ctx.reply(`⏳ Please wait ${wait}s before sending another link.`);

  // Only download the first supported URL
  const url = urls.find((u) => identifyPlatform(u));
  if (!url)
    return ctx.reply("❌ Unsupported platform. Use /help to see the list.");

  const platform = identifyPlatform(url);
  const statusMsg = await ctx.replyWithHTML(
    `⏳ Downloading from <b>${platform}</b>...\n<i>Please wait.</i>`,
    { reply_to_message_id: ctx.message.message_id }
  );

  let acquired = false;
  try {
    await queue.acquire(ctx.from.id);
    acquired = true;
    stats.recordAttempt();

    const result = await download(url, {
      skipSizeLimit: PLATFORMS_NO_SIZE_LIMIT.has(platform),
    });
    const { filePath, title, duration, uploader, fileSize, isLargeFile } = result;

    const mins = Math.floor(duration / 60);
    const secs = String(Math.floor(duration % 60)).padStart(2, "0");
    const caption =
      `🎬 <b>${escapeHtml(title)}</b>\n` +
      `👤 ${escapeHtml(uploader)}  •  📱 ${platform}` +
      (duration ? `  •  ⏱ ${mins}:${secs}` : "") +
      `\n📦 ${formatBytes(fileSize)}`;

    await ctx.telegram.editMessageText(
      ctx.chat.id, statusMsg.message_id, undefined,
      isLargeFile
        ? "📤 Uploading large file via local API... (this may take a minute)"
        : "📤 Uploading..."
    );

    await ctx.replyWithVideo(
      { source: createReadStream(filePath) },
      {
        caption,
        parse_mode: "HTML",
        supports_streaming: true,
        reply_to_message_id: ctx.message.message_id,
      }
    );

    cleanup(filePath);
    stats.recordSuccess(platform, ctx.from.id);

    await ctx.telegram.deleteMessage(ctx.chat.id, statusMsg.message_id);

  } catch (err) {
    let text;
    if (err instanceof FileTooLargeError) {
      stats.recordTooLarge();
      text = `❌ <b>File too large</b>\n\n${escapeHtml(err.message)}`;
    } else if (err instanceof DownloadError) {
      stats.recordFailure();
      text = `❌ <b>Download failed</b>\n\n${escapeHtml(err.message)}`;
    } else {
      stats.recordFailure();
      console.error("Unexpected error:", err);
      text = "❌ <b>An unexpected error occurred.</b>";
    }
    await ctx.telegram.editMessageText(
      ctx.chat.id, statusMsg.message_id, undefined, text, { parse_mode: "HTML" }
    );
  } finally {
    if (acquired) queue.release(ctx.from.id);
  }
});

// ── Error handler ────────────────────────────────────────────────
bot.catch((err, ctx) => console.error(`[${ctx.updateType}]`, err));

// ── Launch ───────────────────────────────────────────────────────
await bot.telegram.setMyCommands([
  { command: "start",  description: "Welcome message" },
  { command: "id",     description: "Get your Telegram user ID" },
  { command: "help",   description: "How to use the bot" },
  { command: "status", description: "Bot queue status" },
  { command: "stats",  description: "Statistics (admin only)" },
]);

bot.launch({ dropPendingUpdates: true });
console.log("✅ Bot is running...");

process.once("SIGINT",  () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));

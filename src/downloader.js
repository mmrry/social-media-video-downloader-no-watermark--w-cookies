import { execFile } from "child_process";
import { promisify } from "util";
import { statSync, readdirSync, unlinkSync, existsSync } from "fs";
import { join } from "path";
import { randomBytes } from "crypto";

import {
  DOWNLOAD_DIR, COOKIES_FILE,
  TELEGRAM_CLOUD_LIMIT_BYTES, TELEGRAM_CLOUD_LIMIT_MB,
  LOCAL_BOT_API_URL, MAX_LARGE_FILE_SIZE_BYTES, MAX_LARGE_FILE_SIZE_MB,
} from "./config.js";

const execFileAsync = promisify(execFile);

export class DownloadError extends Error { }
export class FileTooLargeError extends Error { }

const USER_AGENT =
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

/**
 * Download video + audio (merged to MP4) using yt-dlp.
 */
export async function download(url, { skipSizeLimit = false } = {}) {
	const fileId = randomBytes(6).toString("hex");
	const outTemplate = join(DOWNLOAD_DIR, `${fileId}.%(ext)s`);

	// разный формат для Twitch — у него нет потоков с кодеком avc1):
	const isTwitch = /twitch\.tv/i.test(url);
	const formatArg = isTwitch
	? "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
	: "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best";
	
	const args = [
		// Prefer H.264 video + AAC audio — compatible with all phones, PCs and Telegram.
		// Falls back to any mp4 combo, then anything available.
		"--format", formatArg,
		"--merge-output-format", "mp4",

		// Re-encode to H.264+AAC to guarantee playback on every device
		"--postprocessor-args",
		"ffmpeg:-c:v libx264 -c:a aac -movflags +faststart -preset fast -crf 23",

		"--no-playlist",
		"--socket-timeout", "30",
		"--retries", "10",
		"--fragment-retries", "10",
		"--geo-bypass",
		"--user-agent", USER_AGENT,
		"--output", outTemplate,
		"--print-json",
		"--no-simulate",

		// TikTok-specific: pick no-watermark CDN
		"--extractor-args", "tiktok:api_hostname=api22-normal-c-useast2a.tiktokv.com",
	];

	// If a cookies file is configured and exists, pass it (required for Instagram)
	if (COOKIES_FILE && existsSync(COOKIES_FILE)) {
		args.push("--cookies", COOKIES_FILE);
	}

	args.push(url);

	let stdout = "";
	try {
		({ stdout } = await execFileAsync("yt-dlp", args, {
			timeout: 180_000,
			maxBuffer: 10 * 1024 * 1024,
		}));
	} catch (err) {
		const raw = String(err.stderr || err.message || err);
		// Extract the first meaningful ERROR line from yt-dlp output
		const errLine =
			raw.split("\n").find((l) => l.includes("ERROR:"))?.replace(/^.*ERROR:\s*/, "") ||
			"Download failed — the link may be private or unsupported.";
		throw new DownloadError(errLine);
	}

	// Parse info from the JSON yt-dlp printed
	let info = {};
	try {
		const lastJson = stdout.trim().split("\n").filter((l) => l.startsWith("{")).pop();
		if (lastJson) info = JSON.parse(lastJson);
	} catch { /* non-fatal */ }

	// Find the downloaded file
	const filePath = findFile(DOWNLOAD_DIR, fileId, "mp4");
	if (!filePath) throw new DownloadError("File not found after download.");

	const { size } = statSync(filePath);
	const mb = (b) => (b / 1024 / 1024).toFixed(1);

	// Обычная платформа — жёсткий лимит 50 МБ
	if (!skipSizeLimit && size > TELEGRAM_CLOUD_LIMIT_BYTES) {
		cleanup(filePath);
		throw new FileTooLargeError(
			`File is ${mb(size)} MB — exceeds the ${TELEGRAM_CLOUD_LIMIT_MB} MB Telegram limit.`
		);
	}

	// Большой файл, но local API не настроен
	if (skipSizeLimit && size > TELEGRAM_CLOUD_LIMIT_BYTES && !LOCAL_BOT_API_URL) {
		cleanup(filePath);
		throw new FileTooLargeError(
			`File is ${mb(size)} MB — exceeds the ${TELEGRAM_CLOUD_LIMIT_MB} MB Telegram limit.\n` +
			`LOCAL_BOT_API_URL is not configured.`
		);
	}

	// Абсолютный потолок даже при local API
	if (skipSizeLimit && size > MAX_LARGE_FILE_SIZE_BYTES) {
		cleanup(filePath);
		throw new FileTooLargeError(
			`File is ${mb(size)} MB — exceeds the ${MAX_LARGE_FILE_SIZE_MB} MB hard cap.`
		);
	}

	return {
		filePath,
		title:       info.title    || "Video",
		duration:    info.duration || 0,
		uploader:    info.uploader || info.channel || "Unknown",
		platform:    info.extractor_key || "Unknown",
		fileSize:    size,
		isLargeFile: size > TELEGRAM_CLOUD_LIMIT_BYTES,  // для статус-сообщения
	};

function findFile(dir, prefix, preferredExt) {
	const files = readdirSync(dir).filter((f) => f.startsWith(prefix));
	if (!files.length) return null;
	const preferred = files.find((f) => f.endsWith(`.${preferredExt}`));
	return join(dir, preferred || files[0]);
}

export function cleanup(filePath) {
	try { unlinkSync(filePath); } catch { /* ignore */ }
}

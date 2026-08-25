import { Bot } from "node-telegram-bot-api";
import { config } from "../config.js";

const token = config.telegram.botToken;
const chatId = config.telegram.chatId;

let bot = null;

if (token && chatId) {
    bot = new Bot(token);
    console.log("[telegramService] bot initialized");
} else {
    console.warn("[telegramService] TELEGRAM_BOT_TOKEN/CHAT_ID missing: alerts are disabled");
}

/**
 * Invia una notifica di testo alla chat configurata.
 * Fire-and-forget: non deve mai bloccare o far fallire la logica di allarme.
 */
export async function sendAlertToBot(message) {
    if (!bot) return;
    try {
        await bot.api.sendMessage({ chat_id: chatId, text: `${message}` });
    } catch (err) {
        console.error("[telegramService] failed to send message:", err.message);
    }
}
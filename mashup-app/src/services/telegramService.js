import { Bot } from "node-telegram-bot-api"; // in realtà grammY sotto questo alias
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

export async function sendAlertToBot(message) {
    if (!bot) return;
    try {
        await bot.api.sendMessage({ chat_id: chatId, text: `${message}` });
    } catch (err) {
        console.error("[telegramService] failed to send message:", err.message);
    }
}

let liveLocationMessageId = null;

export async function reportPosition(lat, lon, livePeriodSeconds = 3600) {
    if (!bot) return;
    try {
        if (liveLocationMessageId === null) {
            const message = await bot.api.sendLocation({
                chat_id: chatId,
                latitude: lat,
                longitude: lon,
                live_period: livePeriodSeconds,
            });
            liveLocationMessageId = message.message_id;
        } else {
            await bot.api.editMessageLiveLocation({
                chat_id: chatId,
                message_id: liveLocationMessageId,
                latitude: lat,
                longitude: lon,
            });
        }
    } catch (err) {
        console.error("[telegramService] failed to report position:", err.message);
    }
}

export async function stopLiveLocation() {
    if (!bot || liveLocationMessageId === null) return;
    try {
        await bot.api.stopMessageLiveLocation({ chat_id: chatId, message_id: liveLocationMessageId });
    } catch (err) {
        console.error("[telegramService] failed to stop live location:", err.message);
    } finally {
        liveLocationMessageId = null;
    }
}
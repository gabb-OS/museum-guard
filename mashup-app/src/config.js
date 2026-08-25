export const config = {
    wotControllerUrl: process.env.WOT_CONTROLLER_URL || "http://node-wot:8080",
    sensorThingPath: "/sensor",
    actuatorThingPath: "/actuator",

    telemetryPollMs: parseInt(process.env.TELEMETRY_POLL_MS || "5000", 10),

    telegram: {
        botToken: process.env.TELEGRAM_BOT_TOKEN,
        chatId: process.env.TELEGRAM_BOT_CHAT_ID,
    },
};
export const config = {
    wotControllerUrl: process.env.WOT_CONTROLLER_URL || "http://node-wot:8080",
    sensorThingPath: "/sensor",
    actuatorThingPath: "/actuator",

    telemetryPollMs: parseInt(process.env.TELEMETRY_POLL_MS || "1000", 10),

    telegram: {
        botToken: process.env.TELEGRAM_BOT_TOKEN,
        chatId: process.env.TELEGRAM_BOT_CHAT_ID,
    },

    // Predictive-light service (separate Python container). 
    // Primary source for target brightness; telemetryPoller.js uses reactive fallback only if this fails.
    predictiveLight: {
        url: process.env.PREDICTIVE_LIGHT_URL || "http://predictive-light:8000",
        timeoutMs: parseInt(process.env.PREDICTIVE_LIGHT_TIMEOUT_MS || "3000", 10),
    },
    expressSrv: {
        port: parseInt(process.env.MASHUP_SERVER_API_PORT || "3001", 10),
    }
};
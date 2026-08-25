export const config = {
    wotControllerUrl: process.env.WOT_CONTROLLER_URL || "http://node-wot:8080",
    sensorThingPath: "/sensor",
    actuatorThingPath: "/actuator",

    telemetryPollMs: parseInt(process.env.TELEMETRY_POLL_MS || "5000", 10),

    telegram: {
        botToken: process.env.TELEGRAM_BOT_TOKEN,
        chatId: process.env.TELEGRAM_BOT_CHAT_ID,
    },

    // Servizio predictive-light (container Python separato). E' l'UNICO
    // punto che decide la luminosita' target in condizioni normali;
    // computeTargetBrightness in telemetryPoller.js resta solo come
    // fallback esplicito se questo servizio non risponde.
    predictiveLight: {
        url: process.env.PREDICTIVE_LIGHT_URL || "http://predictive-light:8000",
        timeoutMs: parseInt(process.env.PREDICTIVE_LIGHT_TIMEOUT_MS || "3000", 10),
    },
};
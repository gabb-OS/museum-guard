import { initWotConsumer } from "./clients/wotConsumer.js";
import { Servient } from "@node-wot/core";
import pkg from "@node-wot/binding-http";
const { HttpClientFactory } = pkg;

import { startTelemetryPolling } from "./logic/telemetryPoller.js";
import { registerAlarmHandler } from "./logic/alarmHandler.js";
import { closeInflux } from "./services/influxService.js";
import { startApiServer } from "./api/server.js";

async function main() {
    const servient = new Servient();
    servient.addClientFactory(new HttpClientFactory());
    const wot = await servient.start();

    // Initialize sensor and actuator Things from the WoT Controller
    const { sensor, actuator } = await initWotConsumer(wot);

    // InfluxDB auto-initializes on import. Register clean flush on shutdown to prevent buffer data loss.
    process.on("SIGTERM", async () => { await closeInflux(); process.exit(0); });
    process.on("SIGINT", async () => { await closeInflux(); process.exit(0); });

    // Start telemetry polling for sensor and actuator values
    startTelemetryPolling(sensor, actuator);

    // Register event-driven impact/theft alarm handling
    registerAlarmHandler(sensor, actuator);

    // Start internal REST API to expose actions (e.g., reset alarm) to external clients like Grafana
    startApiServer(sensor, actuator);
}

main().catch(err => {
    console.error("Fatal error in Mash-up app:", err);
    process.exit(1);
});
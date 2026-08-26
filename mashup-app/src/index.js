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

    //Init actuator and sensor Thing from the WoT Controller
    const {sensor, actuator} = await initWotConsumer(wot);

    // InfluxDB si inizializza da solo all'import di influxService.js
    // (client + writeApi creati a module-scope). Qui registriamo solo
    // il flush pulito allo shutdown, per non perdere punti in buffer.
    process.on("SIGTERM", async () => { await closeInflux(); process.exit(0); });
    process.on("SIGINT", async () => { await closeInflux(); process.exit(0); });

    // Req-res polling telemetry both sensor and actuator values
    startTelemetryPolling(sensor, actuator);

    // Pub-Sub Event driven impact-theft alarm handling
    registerAlarmHandler(sensor, actuator);

    // Internal REST API: exposes actions (e.g., reset alarm) to external clients
    // such as Grafana, without requiring them to talk directly to the WoT Controller
    startApiServer(actuator);

}

main().catch(err => {
    console.error("Fatal error in Mash-up app:", err);
    process.exit(1);
});
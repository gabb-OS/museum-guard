import { initWotConsumer } from "./clients/wotConsumer.js";
import { Servient } from "@node-wot/core";
import { HttpClientFactory } from "@node-wot/binding-http";

import { startTelemetryPolling } from "./logic/telemetryPoller.js";
import { registerAlarmHandler } from "./logic/alarmHandler.js";

async function main() {
	const servient = new Servient();  
    servient.addClientFactory(new HttpClientFactory());
    const wot = await servient.start();

    //Init actuator and sensor Thing from the WoT Controller
    const{sensor, actuator} = await initWotConsumer(wot);

    //TODO: Init InfluxDB
    //

    // Req-res polling telemetry both sensor and actuator values
    startTelemetryPolling(sensor, actuator);

    // Pub-Sub Event driven impact-theft alarm handling
    registerAlarmHandler(sensor, actuator);
    
}

main().catch(err => {
    console.error("Fatal error in Mash-up app:", err);
    process.exit(1);
});

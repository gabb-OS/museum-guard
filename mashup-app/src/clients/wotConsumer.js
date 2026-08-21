import { Servient } from "@node-wot/core";
import { HttpClientFactory } from "@node-wot/binding-http";
import { config } from "./config.js";

export async function initWotConsumer() {
    // Create the Servient in Client mode
	const servient = new Servient();  
    servient.addClientFactory(new HttpClientFactory());
    const wot = await servient.start();

	// Consume the sensor Thing
	const sensorUrl = `${config.wotControllerUrl}${config.sensorThingPath}`;
	const senTD = await WoT.requestThingDescription(sensorUrl);
	const sensor = await WoT.consume(senTD);

	console.log(`[mashup-consumer] connesso al sensor Thing (${sensorUrl})`);

	// Consume the actuator Thing
	const actuatorUrl = `${config.wotControllerUrl}${config.actuatorThingPath}`;
	const actTD = await WoT.requestThingDescription(actuatorUrl);
	const actuator = await WoT.consume(actTD);

	console.log(`[mashup-consumer] connesso all'actuator Thing (${actuatorUrl})`);

	return { sensor, actuator };
}

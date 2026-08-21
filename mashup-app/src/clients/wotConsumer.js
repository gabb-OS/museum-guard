import { Servient } from "@node-wot/core";
import { HttpClientFactory } from "@node-wot/binding-http";
import { config } from "./config.js";

export async function initWotConsumer(wot) {
	// Consume the sensor Thing
	const sensorUrl = `${config.wotControllerUrl}${config.sensorThingPath}`;
	const senTD = await wot.requestThingDescription(sensorUrl);
	const sensor = await wot.consume(senTD);

	console.log(`[mashup-consumer] connesso al sensor Thing (${sensorUrl})`);

	// Consume the actuator Thing
	const actuatorUrl = `${config.wotControllerUrl}${config.actuatorThingPath}`;
	const actTD = await wot.requestThingDescription(actuatorUrl);
	const actuator = await wot.consume(actTD);

	console.log(`[mashup-consumer] connesso all'actuator Thing (${actuatorUrl})`);

	return { sensor, actuator };
}

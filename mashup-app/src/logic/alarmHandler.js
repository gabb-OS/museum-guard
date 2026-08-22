/**
alarmEvent e' gia' un evento wot -> no polling, si subscribing
– accidental impact events;
– theft events;
– position events (tracking GPS post-furto, finche' non arriva un reset)
 */

import { writeEvent, writePosition } from "../services/influxService.js";
// import { sendAlert } from "../services/telegramService.js"; // bonus, ancora da implementare

export function registerAlarmHandler(sensor, actuator) {
    sensor.subscribeEvent("alarmEvent", async (data) => {
        const event = await data.value();
        console.log("[ALARM]", event);

        if (event.type === "impact") {
            await writeEvent(event);
            await actuator.invokeAction("triggerImpactBlink");
            // await sendAlert(`IMPACT detected (axis ${event.axis}, value ${event.value})`);
        } else if (event.type === "theft") {
            await writeEvent(event);
            await actuator.invokeAction("triggerTheftAlarm");
            // await sendAlert(`THEFT detected! (axis ${event.axis}, value ${event.value})`);
        } else if (event.type === "position") {
            // Solo tracking/logging, nessuna azione sull'attuatore: la
            // posizione arriva a raffica (ogni ~5s) finche' il furto non
            // viene resettato via sensor.invokeAction("resetTracking").
            await writePosition(event);
        }
    }, (err) => console.error("[ALARM] errore subscribe:", err.message));
}
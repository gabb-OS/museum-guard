/**
alarmEvent e' gia' un evento wot -> no polling, si subscribing
– accidental impact events;
– theft events;
– position events (tracking GPS post-furto, finche' non arriva un reset)
 */

import { writeEvent, writePosition } from "../services/influxService.js";
import { sendAlertToBot, reportPosition } from "../services/telegramService.js";

export function registerAlarmHandler(sensor, actuator) {
    sensor.subscribeEvent("alarmEvent", async (data) => {
        const event = await data.value();
        console.log("[ALARM]", event);

        try {
            if (event.type === "impact") {
            await writeEvent(event);
            await actuator.invokeAction("triggerImpactBlink");
            await sendAlertToBot(`IMPACT detected (axis ${event.axis}, value ${event.value})`);

            } else if (event.type === "theft") {
                await writeEvent(event);
                await actuator.invokeAction("triggerTheftAlarm");
                await sendAlertToBot(`THEFT detected (axis ${event.axis}, value ${event.value})`);

            } else if (event.type === "position") {
                // Solo tracking/logging, nessuna azione sull'attuatore: la
                // posizione arriva (ogni ~5s) finche' il furto non
                // viene resettato via sensor.invokeAction("resetTracking").
                await writePosition(event);
                await reportPosition(event.lat, event.lon);
            }
            } catch (err) {
                console.error("[ALARM] Error handling alarm event:", err.message);
        }
        
    }, (err) => console.error("[ALARM] ssubscribe error:", err.message));
}
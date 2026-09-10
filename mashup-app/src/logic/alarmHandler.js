/**
alarmEvent is already a wot event -> subscribing to:
    – accidental impact events;
    – theft events;
    – position events (post-theft GPS tracking, until a reset arrives) 
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
                // Tracking/logging only, no action on the actuator: the position arrives 
                // (every ~5s) until the theft is reset via sensor.invokeAction("resetTracking").
                await writePosition(event);
                await reportPosition(event.lat, event.lon);
            }
            } catch (err) {
                console.error("[ALARM] Error handling alarm event:", err.message);
        }
        
    }, (err) => console.error("[ALARM] ssubscribe error:", err.message));
}
/**

alarmEvent e' gia un evento wot -> no polling, si subscribing
– accidental impact events;
– theft events;


await event
 if type x
 invoke x.action

 eccetera
 */

//import { writeEvent } from "../services/influxService.js";
import { sendAlert } from "../services/telegramService.js";

export function registerAlarmHandler(sensor, actuator) {
    sensor.subscribeEvent("alarmEvent", async (data) => {
        const event = await data.value();
        console.log("[ALARM]", event);

        //await writeEvent(event);

        //TODO: check if is simple string
        //TODO: check if event type ==  position  makes sense

        if (event.type === "impact") {
            await actuator.invokeAction("triggerImpactBlink");
            //await sendAlert(`IMPACT detected (axis ${event.axis}, value ${event.value})`);
        } else if (event.type === "theft") {
            await actuator.invokeAction("triggerTheftAlarm");
            //await sendAlert(`THEFT detected! (axis ${event.axis}, value ${event.value})`);
        } else if (event.type === "position") {
            //await sendAlert(`Position estimated: ${event.lat}, ${event.lon}`);
        }
    }, (err) => console.error("[ALARM] errore subscribe:", err.message));
}
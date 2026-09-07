/**
alarmEvent e' gia' un evento wot -> no polling, si subscribing
– accidental impact events;
– theft events;
– position events (tracking GPS post-furto, finche' non arriva un reset)

MODIFICA (metriche/test): per la metrica #3 (latenza end-to-end evento ->
notifica Telegram) misuriamo qui due timestamp:
  - receivedTs : quando l'evento arriva al mashup (Date.now()/1000)
  - notifiedTs : subito dopo che sendAlertToBot() e' tornata

Se il sensore (o il mock) include un campo "ts" nel payload dell'evento
(epoch secondi, generato al momento del rilevamento), calcoliamo anche la
tratta "sensore -> mashup". Tutto viene loggato su CSV da metricsLogger.js,
senza toccare la logica di allarme esistente.
 */

import { writeEvent, writePosition } from "../services/influxService.js";
import { sendAlertToBot } from "../services/telegramService.js";
import { logLatency } from "../services/metricsLogger.js";

export function registerAlarmHandler(sensor, actuator) {
    sensor.subscribeEvent("alarmEvent", async (data) => {
        const event = await data.value();
        const receivedTs = Date.now() / 1000;
        console.log("[ALARM]", event);

        try {
            if (event.type === "impact") {
                await writeEvent(event);
                await actuator.invokeAction("triggerImpactBlink");
                await sendAlertToBot(`IMPACT detected (axis ${event.axis}, value ${event.value})`);
                const notifiedTs = Date.now() / 1000;
                logLatency({
                    type: "impact",
                    axis: event.axis,
                    value: event.value,
                    eventTs: event.ts,
                    receivedTs,
                    notifiedTs,
                });

            } else if (event.type === "theft") {
                await writeEvent(event);
                await actuator.invokeAction("triggerTheftAlarm");
                await sendAlertToBot(`THEFT detected (axis ${event.axis}, value ${event.value})`);
                const notifiedTs = Date.now() / 1000;
                logLatency({
                    type: "theft",
                    axis: event.axis,
                    value: event.value,
                    eventTs: event.ts,
                    receivedTs,
                    notifiedTs,
                });

            } else if (event.type === "position") {
                // Solo tracking/logging, nessuna azione sull'attuatore: la
                // posizione arriva a raffica (ogni ~5s) finche' il furto non
                // viene resettato via sensor.invokeAction("resetTracking").
                await writePosition(event);
            }
            } catch (err) {
                console.error("[ALARM] Error handling alarm event:", err.message);
        }

    }, (err) => console.error("[ALARM] ssubscribe error:", err.message));
}
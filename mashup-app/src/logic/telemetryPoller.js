/*
GABE: farei un unico polling loop per leggere
i valori dei sensori e lo stato degli attuatori, POI da scrivere in influx,
- periodically read the sensor measurements from ESP-SEN;
    – ambient light measurements;
    – acceleration values on X/Y/Z axes;

– actuator states (IDLE IMPACT THEFT ???);
– lighting control values (led intensity value ???).
*/

import { writeTelemetry } from "../services/influxService.js";
import { config } from "../config.js";

// Regola lineare semplice: piu' luce ambientale c'e', meno serve
// illuminare artificialmente l'opera. Personalizzabile a piacere.
function computeTargetBrightness(ambientLightPct) {
    const target = Math.round(100 - ambientLightPct);
    return Math.min(100, Math.max(0, target));
}

export function startTelemetryPolling(sensor, actuator) {
    let lastBrightnessSent = null;

    setInterval(async () => {
        try {
            // Sensor
            const lightSens = await (await sensor.readProperty("ambientLight")).value();
            const accelSens = await (await sensor.readProperty("accelerometer")).value();

            // Actuator (nome corretto secondo la TD: artworkLedBrightness)
            const alarmState = await (await actuator.readProperty("alarmLightState")).value();
            const artworkBrightness = await (await actuator.readProperty("artworkLedBrightness")).value();

            await writeTelemetry({ lightSens, accelSens, alarmState, artworkBrightness });

            // Regolazione automatica dell'illuminazione (requisito base traccia)
            const target = computeTargetBrightness(lightSens);
            if (target !== lastBrightnessSent) {
                await actuator.invokeAction("regulateBrightness", target);
                lastBrightnessSent = target;
            }

        } catch (err) {
            console.warn("[TELEMETRY] errore poll:", err.message);
        }
    }, config.telemetryPollMs);
}
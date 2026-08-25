/*
- periodically read the sensor measurements from ESP-SEN;
    – ambient light measurements;
    – acceleration values on X/Y/Z axes;

– actuator states (IDLE IMPACT THEFT);
– lighting control values (led intensity value);

Il polling unico legge sensore+attuatore e scrive tutto in InfluxDB
(requisito base della traccia). Per la regolazione dell'illuminazione
("Predictive Lighting Control", bonus) c'e' UN SOLO writer verso
regulateBrightness per evitare due decisori concorrenti sullo stesso
attuatore:

  1. si chiede la previsione al servizio predictive-light (container a
     parte, vedi predictive-light/): rifitta un modello ARIMA sullo
     storico reale ad ogni ciclo e si autocorregge confrontando le
     proprie previsioni passate coi valori reali osservati poi
     (loop di riconciliazione interno al servizio);
  2. SOLO se quella chiamata fallisce (servizio giu', timeout, nessuna
     previsione ancora pronta) si usa computeTargetBrightness come
     fallback reattivo esplicito.

Non esiste nessun percorso in cui entrambi possano chiamare
regulateBrightness nello stesso ciclo.
*/

import { writeTelemetry } from "../services/influxService.js";
import { getPredictedBrightness } from "../services/predictiveLightService.js";
import { config } from "../config.js";

// Regola lineare semplice usata SOLO come fallback quando il servizio
// predittivo non e' disponibile: piu' luce ambientale c'e', meno serve
// illuminare artificialmente l'opera.
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

            // Regolazione dell'illuminazione: predittivo primo, fallback
            // reattivo SOLO in caso di errore. Un solo invokeAction per ciclo.
            let target;
            try {
                target = await getPredictedBrightness();
            } catch (err) {
                console.warn("[TELEMETRY] predictive-light non disponibile, fallback reattivo:", err.message);
                target = computeTargetBrightness(lightSens);
            }

            if (target !== lastBrightnessSent) {
                await actuator.invokeAction("regulateBrightness", target);
                lastBrightnessSent = target;
            }

        } catch (err) {
            console.warn("[TELEMETRY] polling error:", err.message);
        }
    }, config.telemetryPollMs);
}
/*
- periodically read the sensor measurements from ESP-SEN;
    – ambient light measurements;
    – acceleration values on X/Y/Z axes;

– actuator states (IDLE IMPACT THEFT);
– lighting control values (led intensity value);

Il polling unico legge sensore+attuatore e scrive tutto in InfluxDB
(requisito base della traccia). Per la regolazione dell'illuminazione
("Predictive Lighting Control", bonus) c'e' UN SOLO punto che decide
il valore finale di brightness per l'attuatore: computeTargetBrightness,
chiamata qui sotto UNA SOLA VOLTA per ciclo, sia che la stima di luce
ambientale venga dal servizio predittivo sia che venga dal fotoresistore
(fallback). In questo modo non esistono due strade diverse che possano
divergere su "cosa significa" quel valore.

Flusso:
  1. si chiede la previsione di luce ambientale al servizio predictive-light
     (container a parte, vedi predictive-light/): rifitta un modello ARIMA
     sullo storico reale ad ogni ciclo e si autocorregge confrontando le
     proprie previsioni passate coi valori reali osservati poi
     (loop di riconciliazione interno al servizio). Il servizio ritorna
     SOLO la luce ambientale prevista (%), mai un valore di brightness.
  2. SOLO se quella chiamata fallisce (servizio giu', timeout, nessuna
     previsione ancora pronta) si usa il valore REALE corrente del
     fotoresistore (lightSens) come stima di luce ambientale.
  3. In ENTRAMBI i casi, la stima di luce ambientale (prevista o reale)
     passa per computeTargetBrightness (100 - x) per ottenere il target
     finale da inviare all'attuatore. Un solo invokeAction per ciclo.
*/

import { writeTelemetry, writeThresholds } from "../services/influxService.js";
import { getPredictedAmbientLight } from "../services/predictiveLightService.js";
import { config } from "../config.js";

// Unico punto che converte "luce ambientale (%)" in "target di
// illuminazione artificiale (%)": piu' luce ambientale c'e', meno
// serve illuminare artificialmente l'opera. Usata sempre, sia con la
// stima predittiva che con il valore reale di fallback.
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

            const thresholds = await (await sensor.readProperty("thresholds")).value();

            await writeTelemetry({ lightSens, accelSens, alarmState, artworkBrightness });
            await writeThresholds(thresholds);

            // Stima della luce ambientale da usare per la regolazione:
            // predittiva se disponibile, altrimenti valore reale corrente.
            let ambientEstimate;
            try {
                ambientEstimate = await getPredictedAmbientLight();
            } catch (err) {
                console.warn("[TELEMETRY] predictive-light non disponibile, fallback su valore reale:", err.message);
                ambientEstimate = lightSens;
            }

            // Unico punto di conversione luce ambientale -> brightness attuatore.
            const target = computeTargetBrightness(ambientEstimate);

            if (target !== lastBrightnessSent) {
                await actuator.invokeAction("regulateBrightness", target);
                lastBrightnessSent = target;
            }

        } catch (err) {
            console.warn("[TELEMETRY] polling error:", err.message);
        }
    }, config.telemetryPollMs);
}
/*
Polls sensor and actuator data, writing to InfluxDB.

Brightness control uses a SINGLE writer and a SINGLE conversion point to
prevent conflicts and drift between the predictive and fallback paths:
  1. Attempts predictive-light service (ARIMA-based) to get the predicted
     ambient light level (%). The service NEVER returns a brightness value,
     only ambient light.
  2. Falls back to the real, current ambient light reading (lightSens)
     ONLY on failure (service down, timeout, cold start).
  3. In BOTH cases, the resulting ambient light estimate (predicted or
     real) is passed through computeTargetBrightness (100 - x) exactly
     once, here, to obtain the final target sent to the actuator.

Error handling: each group of reads (sensor / actuator / thresholds) is
wrapped in its own try/catch. This way a single failing readProperty
(e.g. the actuator being briefly unreachable) doesn't abort the whole
cycle and doesn't hide unrelated data that was successfully read in the
same tick (e.g. sensor readings that DID succeed).
*/

import { writeTelemetry, writeThresholds, writePredictedLight } from "../services/influxService.js";
import { getPredictedAmbientLight } from "../services/predictiveLightService.js";
import { config } from "../config.js";

// Simple linear fallback: higher ambient light means lower artificial lighting needed.
function computeTargetBrightness(ambientLightPct) {
    const target = Math.round(100 - ambientLightPct);
    return Math.min(100, Math.max(0, target));
}

export function startTelemetryPolling(sensor, actuator) {
    let lastBrightnessSent = null;
    let consecutiveErrors = 0;

    setInterval(async () => {
        const tickStart = new Date().toISOString();

        let lightSens, accelSens;
        let alarmState, artworkBrightness;
        let thresholds;

        // --- Sensor reads (ambient light + accelerometer) ---
        try {
            lightSens = await (await sensor.readProperty("ambientLight")).value();
            accelSens = await (await sensor.readProperty("accelerometer")).value();
        } catch (err) {
            consecutiveErrors++;
            console.warn(`[TELEMETRY][${tickStart}] sensor read failed (#${consecutiveErrors}):`, err.message);
        }

        // --- Actuator reads (alarm state + brightness) ---
        try {
            alarmState = await (await actuator.readProperty("alarmLightState")).value();
            artworkBrightness = await (await actuator.readProperty("artworkLedBrightness")).value();
        } catch (err) {
            consecutiveErrors++;
            console.warn(`[TELEMETRY][${tickStart}] actuator read failed (#${consecutiveErrors}):`, err.message);
        }

        // --- Thresholds read ---
        try {
            thresholds = await (await sensor.readProperty("thresholds")).value();
        } catch (err) {
            consecutiveErrors++;
            console.warn(`[TELEMETRY][${tickStart}] thresholds read failed (#${consecutiveErrors}):`, err.message);
        }

        // --- Persist whatever we managed to read this tick ---
        if (lightSens !== undefined && accelSens !== undefined && alarmState !== undefined && artworkBrightness !== undefined) {
            try {
                await writeTelemetry({ lightSens, accelSens, alarmState, artworkBrightness });
            } catch (err) {
                console.warn(`[TELEMETRY][${tickStart}] writeTelemetry failed:`, err.message);
            }
        } else {
            console.warn(`[TELEMETRY][${tickStart}] skipping writeTelemetry: incomplete data this tick`);
        }

        if (thresholds !== undefined) {
            try {
                await writeThresholds(thresholds);
            } catch (err) {
                console.warn(`[TELEMETRY][${tickStart}] writeThresholds failed:`, err.message);
            }
        }

        // --- Brightness regulation: only possible if we have a sensor reading ---
        if (lightSens === undefined) {
            console.warn(`[TELEMETRY][${tickStart}] skipping brightness regulation: no ambient light reading available`);
            return;
        }

        // Ambient light estimate to use for the regulation: predicted
        // if available, otherwise the real current reading.
        let ambientEstimate;
        let predictionOk = false;
        try {
            ambientEstimate = await getPredictedAmbientLight();
            predictionOk = true;
        } catch (err) {
            console.warn(`[TELEMETRY][${tickStart}] predictive-light unavailable, using reactive fallback:`, err.message);
            ambientEstimate = lightSens;
        }

        // Write the predicted value to Influx only when it's genuinely from the
        // predictive service (not the fallback), so predicted_light only ever
        // contains real predictions, never a copy of the reactive reading.
        if (predictionOk) {
            try {
                await writePredictedLight(ambientEstimate);
            } catch (err) {
                console.warn(`[TELEMETRY][${tickStart}] writePredictedLight failed:`, err.message);
            }
        }

        // Single conversion point, always applied here. Single invokeAction per cycle.
        const target = computeTargetBrightness(ambientEstimate);

        if (target !== lastBrightnessSent) {
            try {
                await actuator.invokeAction("regulateBrightness", target);
                lastBrightnessSent = target;
                consecutiveErrors = 0; // reset once a full cycle succeeds end-to-end
            } catch (err) {
                consecutiveErrors++;
                console.warn(`[TELEMETRY][${tickStart}] regulateBrightness failed (#${consecutiveErrors}):`, err.message);
            }
        } else {
            consecutiveErrors = 0;
        }

    }, config.telemetryPollMs);
}
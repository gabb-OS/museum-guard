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
*/

import { writeTelemetry, writeThresholds } from "../services/influxService.js";
import { getPredictedAmbientLight } from "../services/predictiveLightService.js";
import { config } from "../config.js";

// Simple linear fallback: higher ambient light means lower artificial lighting needed.
function computeTargetBrightness(ambientLightPct) {
    const target = Math.round(100 - ambientLightPct);
    return Math.min(100, Math.max(0, target));
}

export function startTelemetryPolling(sensor, actuator) {
    let lastBrightnessSent = null;

    setInterval(async () => {
        try {
            // Read sensor data
            const lightSens = await (await sensor.readProperty("ambientLight")).value();
            const accelSens = await (await sensor.readProperty("accelerometer")).value();

            // Read actuator data (artworkLedBrightness per TD)
            const alarmState = await (await actuator.readProperty("alarmLightState")).value();
            const artworkBrightness = await (await actuator.readProperty("artworkLedBrightness")).value();

            const thresholds = await (await sensor.readProperty("thresholds")).value();

            await writeTelemetry({ lightSens, accelSens, alarmState, artworkBrightness });
            await writeThresholds(thresholds);

            // Ambient light estimate to use for the regulation: predicted
            // if available, otherwise the real current reading.
            let ambientEstimate;
            try {
                ambientEstimate = await getPredictedAmbientLight();
            } catch (err) {
                console.warn("[TELEMETRY] predictive-light unavailable, using reactive fallback:", err.message);
                ambientEstimate = lightSens;
            }

            // Single conversion point, always applied here. Single invokeAction per cycle.
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
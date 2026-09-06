/*
Polls sensor and actuator data, writing to InfluxDB.
Brightness control uses a single writer to prevent conflicts:
1. Attempts predictive-light service (ARIMA-based).
2. Falls back to reactive computeTargetBrightness ONLY on failure.
*/

import { writeTelemetry, writeThresholds } from "../services/influxService.js";
import { getPredictedBrightness } from "../services/predictiveLightService.js";
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
            
            // Brightness control: predictive first, reactive fallback only on error. Single invokeAction per cycle.
            let target;
            try {
                target = await getPredictedBrightness();
            } catch (err) {
                console.warn("[TELEMETRY] predictive-light unavailable, using reactive fallback:", err.message);
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
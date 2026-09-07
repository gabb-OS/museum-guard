/*
Adapter for the external predictive-light service (Python/FastAPI).

Throws an error if the request fails (service down, timeout, cold start 503),
letting telemetryPoller.js handle the reactive fallback.
*/

import { config } from "../config.js";

const PREDICT_TIMEOUT_MS = config.predictiveLight.timeoutMs;

export async function getPredictedAmbientLight() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), PREDICT_TIMEOUT_MS);

    try {
        const res = await fetch(`${config.predictiveLight.url}/predict`, {
            signal: controller.signal,
        });

        if (!res.ok) {
            throw new Error(`predictive-light responded with ${res.status}`);
        }

        const body = await res.json();
        if (typeof body.predicted_ambient_light !== "number") {
            throw new Error("predictive-light response missing valid 'predicted_ambient_light' field");
        }

        return body.predicted_ambient_light;
    } finally {
        clearTimeout(timeout);
    }
}
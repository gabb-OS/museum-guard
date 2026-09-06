/*
Adapter for the external predictive-light service (Python/FastAPI).
Does not calculate brightness itself; just fetches the latest prediction.

Throws an error if the request fails (service down, timeout, cold start 503),
letting telemetryPoller.js handle the reactive fallback.
Ensures a single source of truth for the actual brightness value.
*/

import { config } from "../config.js";

const PREDICT_TIMEOUT_MS = config.predictiveLight.timeoutMs;

export async function getPredictedBrightness() {
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
        if (typeof body.brightness !== "number") {
            throw new Error("predictive-light response missing valid 'brightness' field");
        }

        return body.brightness;
    } finally {
        clearTimeout(timeout);
    }
}
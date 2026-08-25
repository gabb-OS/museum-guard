/*
Adapter verso il servizio predictive-light (container Python/FastAPI a
parte, vedi predictive-light/). Non fa MAI il calcolo lui stesso: si
limita a chiedere al servizio l'ultima previsione gia' pronta.

Se la chiamata fallisce (servizio giu', timeout, nessuna previsione
ancora disponibile allo startup a freddo -> 503) lancia un errore e
lascia che sia telemetryPoller.js a decidere il fallback reattivo.
Questo e' il punto chiave del design: un solo posto (telemetryPoller)
decide quale valore va effettivamente su regulateBrightness, questo
modulo si limita a "chiedere" al predittivo.
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
            throw new Error(`predictive-light ha risposto ${res.status}`);
        }

        const body = await res.json();
        if (typeof body.brightness !== "number") {
            throw new Error("risposta predictive-light senza campo 'brightness' valido");
        }

        return body.brightness;
    } finally {
        clearTimeout(timeout);
    }
}
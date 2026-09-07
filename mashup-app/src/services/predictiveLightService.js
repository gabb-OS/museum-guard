/*
Adapter verso il servizio predictive-light (container Python/FastAPI a
parte, vedi predictive-light/). Non fa MAI il calcolo lui stesso: si
limita a chiedere al servizio l'ultima previsione di luce ambientale
gia' pronta.

IMPORTANTE: questo modulo restituisce la luce ambientale PREVISTA (%),
NON un valore di brightness per l'attuatore. La conversione (100 - x)
e' responsabilita' esclusiva di telemetryPoller.js, che e' l'UNICO
punto che decide cosa va effettivamente su regulateBrightness, sia in
caso di successo che di fallback sul valore reale del fotoresistore.

Se la chiamata fallisce (servizio giu', timeout, nessuna previsione
ancora disponibile allo startup a freddo -> 503) lancia un errore e
lascia che sia telemetryPoller.js a decidere il fallback.
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
            throw new Error(`predictive-light ha risposto ${res.status}`);
        }

        const body = await res.json();
        if (typeof body.predicted_ambient_light !== "number") {
            throw new Error("risposta predictive-light senza campo 'predicted_ambient_light' valido");
        }

        return body.predicted_ambient_light;
    } finally {
        clearTimeout(timeout);
    }
}
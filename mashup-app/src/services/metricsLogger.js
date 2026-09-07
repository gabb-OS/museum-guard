/*
metricsLogger.js

Piccolo helper "append-only" per loggare su CSV le metriche richieste per i
test (metrica #3: latenza end-to-end evento -> notifica Telegram).

Scrive in LOG_DIR (env var, default /app/logs dentro il container). Ricorda
di montare un volume su quella cartella nel docker-compose se vuoi leggere
i CSV dall'host (vedi docker-compose.yml aggiornato).

Non lancia mai eccezioni verso il chiamante: un errore di logging non deve
mai far fallire la logica di allarme.
*/

import fs from "fs";
import path from "path";

const LOG_DIR = process.env.LOG_DIR || "/app/logs";

try {
    if (!fs.existsSync(LOG_DIR)) {
        fs.mkdirSync(LOG_DIR, { recursive: true });
    }
} catch (err) {
    console.error("[metricsLogger] impossibile creare LOG_DIR:", err.message);
}

const LATENCY_CSV = path.join(LOG_DIR, "latency_log.csv");
const LATENCY_HEADER =
    "type,axis,value,event_ts,received_ts,notified_ts," +
    "latency_event_to_received_ms,latency_received_to_notified_ms,latency_event_to_notified_ms\n";

function ensureHeader() {
    try {
        if (!fs.existsSync(LATENCY_CSV)) {
            fs.writeFileSync(LATENCY_CSV, LATENCY_HEADER);
        }
    } catch (err) {
        console.error("[metricsLogger] impossibile creare latency_log.csv:", err.message);
    }
}
ensureHeader();

/**
 * Logga una riga di latenza end-to-end per un evento impact/theft.
 *
 * @param {Object} params
 * @param {string} params.type       - "impact" | "theft"
 * @param {string} [params.axis]     - asse riportato dal sensore
 * @param {number} [params.value]    - valore riportato dal sensore
 * @param {number|undefined} params.eventTs   - timestamp (epoch secondi, float) di generazione
 *                                              dell'evento lato sensore (campo "ts" nel
 *                                              payload CoAP). Puo' essere undefined se il
 *                                              mock/firmware non lo fornisce: in quel caso
 *                                              la tratta "sensore->mashup" viene omessa.
 * @param {number} params.receivedTs - Date.now()/1000 al momento della ricezione lato mashup
 * @param {number} params.notifiedTs - Date.now()/1000 subito dopo che sendAlertToBot e' tornato
 */
export function logLatency({ type, axis, value, eventTs, receivedTs, notifiedTs }) {
    const hasEventTs = typeof eventTs === "number" && !Number.isNaN(eventTs);

    const l1 = hasEventTs ? (receivedTs - eventTs) * 1000 : "";
    const l2 = (notifiedTs - receivedTs) * 1000;
    const l3 = hasEventTs ? (notifiedTs - eventTs) * 1000 : "";

    const row = [
        type,
        axis ?? "",
        value ?? "",
        hasEventTs ? eventTs.toFixed(6) : "",
        receivedTs.toFixed(6),
        notifiedTs.toFixed(6),
        typeof l1 === "number" ? l1.toFixed(1) : "",
        l2.toFixed(1),
        typeof l3 === "number" ? l3.toFixed(1) : "",
    ].join(",") + "\n";

    fs.appendFile(LATENCY_CSV, row, (err) => {
        if (err) {
            console.error("[metricsLogger] errore scrittura latency_log.csv:", err.message);
        }
    });
}
import axios from "axios";

const DEFAULT_HOST = process.env.ESP_ACT_ADDRESS || "esp-act-mock";
const DEFAULT_PORT = parseInt(process.env.HTTP_PORT || "80", 10);
const DEFAULT_BASE = `http://${DEFAULT_HOST}:${DEFAULT_PORT}`;
const REQUEST_TIMEOUT_MS = 3000; // regolabile

let requestQueue = Promise.resolve();

function serialize(fn) {
    const result = requestQueue.then(fn, fn);
    // Previene la rottura della coda se una richiesta fallisce
    requestQueue = result.catch(() => {}); 
    return result;
}

let inFlightState = null;


export async function getActuatorState(base = DEFAULT_BASE) {
    // Se c'è già una richiesta in volo, restituisci quella (zero chiamate HTTP extra)
    if (inFlightState) return inFlightState;

    // Altrimenti, crea una nuova richiesta e mettila in coda
    inFlightState = serialize(async () => {
        try {
            const resp = await axios.get(`${base}/state`, { timeout: REQUEST_TIMEOUT_MS });
            return resp.data;
        } catch (err) {
            handleDeviceError(err, "fetching actuator state");
            throw err;
        } finally {
            inFlightState = null; // Libera il lock per la prossima lettura
        }
    });

    return inFlightState;
}

export async function setBrightness(value, base = DEFAULT_BASE) {
    return serialize(async () => {
        const safeValue = Math.min(100, Math.max(0, value));
        try {
            await axios.post(`${base}/ambientlight`, { brightness: safeValue }, { timeout: REQUEST_TIMEOUT_MS });
        } catch (err) {
            handleDeviceError(err, "setting brightness");
            throw err;
        }
    });
}

export async function startBlink(base = DEFAULT_BASE) {
    try {
        await axios.post(`${base}/impact`, null, { timeout: REQUEST_TIMEOUT_MS });
    } catch (err) {
        handleDeviceError(err, "triggering Impact Alarm");
    }
}

export async function activateAlarm(base = DEFAULT_BASE) {
    return serialize(async () => {
        try {
            await axios.post(`${base}/theft`, {}, { timeout: REQUEST_TIMEOUT_MS });
        } catch (err) {
            handleDeviceError(err, "activating theft alarm");
            throw err;
        }
    });
}


export async function resetAlarms(base = DEFAULT_BASE) {
    return serialize(async () => {
        try {
            await axios.post(`${base}/reset`, {}, { timeout: REQUEST_TIMEOUT_MS });
        } catch (err) {
            handleDeviceError(err, "resetting alarms");
            throw err;
        }
    });
}

function handleDeviceError(err, context) {
    if (err.response) {
        console.error(`[ESP_ACT] Device error ${context}: ${err.response.status} - ${err.response.statusText}`);
    } else if (err.request) {
        console.error(`[ESP_ACT] Network error ${context}: No response received`);
    } else {
        console.error(`[ESP_ACT] Error ${context}:`, err.message);
    }
}
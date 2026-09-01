import axios from "axios";

const DEFAULT_HOST = process.env.ESP_ACT_ADDRESS || "esp-act-mock";
const DEFAULT_PORT = parseInt(process.env.HTTP_PORT || "80", 10);
const DEFAULT_BASE = `http://${DEFAULT_HOST}:${DEFAULT_PORT}`;
const REQUEST_TIMEOUT_MS = 3000; // regolabile

export async function getActuatorState(base = DEFAULT_BASE) {
    try {
        const resp = await axios.get(`${base}/state`, { timeout: REQUEST_TIMEOUT_MS });
        return resp.data;
    } catch (err) {
        handleDeviceError(err, " fetching actuator state");
    }
}

export async function setBrightness(value, base = DEFAULT_BASE) {
    const safeValue = Math.min(100, Math.max(0, value));
    try {
        await axios.post(`${base}/ambientlight`, { brightness: safeValue }, { timeout: REQUEST_TIMEOUT_MS });
    } catch (err) {
        handleDeviceError(err, "setting brightness");
    }
}

export async function startBlink(base = DEFAULT_BASE) {
    try {
        await axios.post(`${base}/impact`, null, { timeout: REQUEST_TIMEOUT_MS });
    } catch (err) {
        handleDeviceError(err, "triggering Impact Alarm");
    }
}

export async function activateAlarm(base = DEFAULT_BASE) {
    try {
        await axios.post(`${base}/theft`, null, { timeout: REQUEST_TIMEOUT_MS });
    } catch (err) {
        handleDeviceError(err, "activating Theft Alarm");
    }
}

export async function resetAlarms(base = DEFAULT_BASE) {
    try {
        await axios.post(`${base}/reset`, null, { timeout: REQUEST_TIMEOUT_MS });
    } catch (err) {
        handleDeviceError(err, "resetting alarms");
    }
}

function handleDeviceError(err, actionDescription) {
    if (err.response) {
        const deviceMsg = err.response.data?.message || 'No additional info';
        throw new Error(`ESP32 Error (${actionDescription}): Device returned ${err.response.status} - ${deviceMsg}`);
    } else if (err.code === "ECONNABORTED") {
        // axios usa questo codice specifico quando scatta il timeout
        throw new Error(`ESP32 Error (${actionDescription}): Device timed out after ${REQUEST_TIMEOUT_MS}ms.`);
    } else if (err.request) {
        throw new Error(`ESP32 Error (${actionDescription}): Device is unreachable or offline.`);
    } else {
        throw new Error(`ESP32 Error (${actionDescription}): ${err.message}`);
    }
}
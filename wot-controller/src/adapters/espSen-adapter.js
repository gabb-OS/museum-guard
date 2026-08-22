import { CoapClient } from "../coap/coap-client.js";

const DEFAULT_HOST = process.env.ESP_SEN_ADDRESS || "esp-sen-mock";
const DEFAULT_PORT = parseInt(process.env.COAP_PORT || "5683", 10);
const client = new CoapClient(DEFAULT_HOST, DEFAULT_PORT);

export async function getLightLevel() {
    try {
        const raw = await client.get("/light");
        return parseFloat(raw);
    } catch (err) {
        handleDeviceError(err, "fetching light level");
    }
}

export async function getAccelReading() {
    try {
        const raw = await client.get("/accel");
        return JSON.parse(raw);
    } catch (err) {
        handleDeviceError(err, "fetching accelerometer reading");
    }
}

export function subscribeToAlarmEvents(onEvent, onError) {
    return client.observe("/events", (text) => {
        try {
            onEvent(JSON.parse(text));
        } catch (err) {
            console.warn("[ESP_SEN adapter] payload evento non valido:", text);
        }
    }, onError);
}

export async function getThresholds() {
    try {
        const raw = await client.get("/thresholds");
        return JSON.parse(raw);
    } catch (err) {
        handleDeviceError(err, "fetching detection thresholds");
    }
}

export async function setImpactThreshold(value) {
    try {
        const res = await client.put("/thresholds/impact", String(value));
        return res.code; // 2.04 = COAP_RESPONSE_CODE_CHANGED
    } catch (err) {
        handleDeviceError(err, "setting impact threshold");
    }
}

export async function setTheftThreshold(value) {
    try {
        const res = await client.put("/thresholds/theft", String(value));
        return res.code;
    } catch (err) {
        handleDeviceError(err, "setting theft threshold");
    }
}

// PUT /reset_alarm: ricalibra la baseline e ferma il tracking GPS
// (gia' esposto sia dal firmware reale che dal mock Python).
export async function resetTracking() {
    try {
        const res = await client.put("/reset_alarm");
        return res.code; // 2.04 = COAP_RESPONSE_CODE_CHANGED
    } catch (err) {
        handleDeviceError(err, "resetting tracking/GPS");
    }
}

function handleDeviceError(err, actionDescription) {
    throw new Error(`ESP_SEN Error (${actionDescription}): ${err.message}`);
}
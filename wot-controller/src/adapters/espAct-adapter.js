import axios from "axios";

const DEFAULT_HOST = process.env.ESP_ACT_ADDRESS || "esp-act-mock";
const DEFAULT_PORT = parseInt(process.env.HTTP_PORT || "80", 10);
const DEFAULT_BASE = `http://${DEFAULT_HOST}:${DEFAULT_PORT}`;

export async function getActuatorState(base = DEFAULT_BASE) {
    try {
		const resp = await axios.get(`${base}/state`)
		return resp.data
	} catch (err) {
		handleDeviceError(err, " fetching actuator state");
	}
}

export async function setBrightness(value, base = DEFAULT_BASE) {
	const safeValue = Math.min(100, Math.max(0, value));
	try {
		const resp = await axios.post(`${base}/ambientlight`, { brightness: safeValue })
	} catch (err) {
		handleDeviceError(err, "setting brightness");
	}
}

export async function startBlink(base = DEFAULT_BASE) {
	try {
		const resp = await axios.post(`${base}/impact`)
	} catch (err) {
		handleDeviceError(err, "triggering Impact Alarm");
	}
}

export async function activateAlarm(base = DEFAULT_BASE) {
	try {
		const resp = await axios.post(`${base}/theft`)
	} catch (err) {
		handleDeviceError(err, "activating Theft Alarm");
	}
}

export async function resetAlarms(base = DEFAULT_BASE) {
	try {
		const resp = await axios.post(`${base}/reset`)
	} catch (err) {
		handleDeviceError(err, "resetting alarms");
	}
}

function handleDeviceError(err, actionDescription) {
    if (err.response) {
        // The ESP32 received the request but returned an error
        const deviceMsg = err.response.data?.message || 'No additional info';
        throw new Error(`ESP32 Error (${actionDescription}): Device returned ${err.response.status} - ${deviceMsg}`);
    } else if (err.request) {
        // The request was sent, but the ESP32 never replied
        throw new Error(`ESP32 Error (${actionDescription}): Device is unreachable, offline, or timed out.`);
    } else {
        // Something else went wrong
        throw new Error(`ESP32 Error (${actionDescription}): ${err.message}`);
    }
}
import axios from "axios";

//const DEFAULT_BASE = process.env.ESP32_BASE_URL
const DEFAULT_BASE = "http://10.78.189.198" // TODO: da configurare in modo più flessibile (env, config file, etc.);

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





/**
 * 





    // ACTIONS
    espActThing.setActionHandler("regulateBrightness", async (params) => {
        const value = await params.value();
        const res = await fetch(`${BASE_URL}/ambientlight`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ brightness: value })
        });
        if (!res.ok) throw new Error(`ESP-ACT error: ${res.status}`);

        currentLightLevel = value;
        espActThing.emitPropertyChange("ambientLightBrightness");
    });

    espActThing.setActionHandler("triggerImpactBlink", async () => {
        await fetch(`${BASE_URL}/impact`, { method: "POST" });
        alarmLightState = true;
        espActThing.emitPropertyChange("alarmLightState");
        // TODO: timer lato controller per rimettere alarmLightState=false dopo 20s
        // OPPURE un event in arrivo da esp
    });
    espActThing.setActionHandler("triggerTheftAlarm", async () => {
        await fetch(`${BASE_URL}/theft`, { method: "POST" });
        alarmLightState = true;
        espActThing.emitPropertyChange("alarmLightState");
    });
    espActThing.setActionHandler("resetAlarm", async () => {
        await fetch(`${BASE_URL}/reset`, { method: "POST" });
        alarmLightState = false;
        espActThing.emitPropertyChange("alarmLightState");
    });

 */
import { getActuatorState, setBrightness, startBlink, activateAlarm, resetAlarms } from "../adapters/espAct-adapter.js";

export async function createEspActTD(WoT) {
    const espActThing = await WoT.produce({
        title: "actuator",
        description: "Actuator node of MuseumGuard",
        properties: {
            artworkLedBrightness: {
                type: "integer",
                description: "Artwork illumination LED level",
                observable: true,
                readOnly: true
            },
            alarmLightState: {
                type: "string",
                description: "Impact/Theft Warning LED state",
                observable: true,
                readOnly: true,
                enum: ["IDLE", "IMPACT", "THEFT"],
                default: "IDLE",
            },
        },
        actions: {
            regulateBrightness: {
                description: "Modify artwork illumination level",
                input: {
                    type: "number",
                    minimum: 0,
                    maximum: 100
                }
            },
            triggerImpactBlink: {
                description: "Blink alarm LED for 20s (impact response)"
            },
            triggerTheftAlarm: {
                description: "Turn alarm LED permanently on (theft response)"
            },
            resetAlarmLight: {
                description: "Turns off the Impact/Theft Warning LED"
            },
        }
    });

    // 1. State cache to prevent network failures from blocking readProperty
    let cachedState = { brightness: 0, alarmState: "IDLE" };

    // 2. Periodic background state polling (with try/catch, like espSenThing)
    setInterval(async () => {
        try {
            const state = await getActuatorState();
            cachedState = state;
            espActThing.emitPropertyChange("artworkLedBrightness");
            espActThing.emitPropertyChange("alarmLightState");
        } catch (err) {
            console.warn("[ESP_ACT] state poll error:", err.message);
        }
    }, 1000);

    // 3. PROPERTIES with safe fallback (read from cache, NO synchronous network calls)
    espActThing.setPropertyReadHandler("artworkLedBrightness", async () => {
        return cachedState.brightness;
    });

    espActThing.setPropertyReadHandler("alarmLightState", async () => {
        return cachedState.alarmState;
    });

    // 4. ACTIONS
    espActThing.setActionHandler("regulateBrightness", async (params) => {
        const rawValue = await params.value();

        // Centralized normalization: callers can send floats or out-of-range values. 
        // regulateBrightness enforces the correct physical value.
        const brightness = Math.round(Math.min(100, Math.max(0, rawValue)));

        await setBrightness(brightness);
        cachedState.brightness = brightness; // Update cache for immediate consistency
        espActThing.emitPropertyChange("artworkLedBrightness");
        return;
    });

    espActThing.setActionHandler("triggerImpactBlink", async () => {
        await startBlink();
        cachedState.alarmState = "IMPACT";
        espActThing.emitPropertyChange("alarmLightState");
    });

    espActThing.setActionHandler("triggerTheftAlarm", async () => {
        await activateAlarm();
        cachedState.alarmState = "THEFT";
        espActThing.emitPropertyChange("alarmLightState");
    });

    espActThing.setActionHandler("resetAlarmLight", async () => {
        await resetAlarms();
        cachedState.alarmState = "IDLE";
        espActThing.emitPropertyChange("alarmLightState");
    });

    await espActThing.expose();
    return espActThing;
}
// Export a function that takes the WoT object as an argument
import {getActuatorState, setBrightness, startBlink, activateAlarm, resetAlarms} from "../adapters/espAct-adapter.js"

export async function createEspActTD(WoT) {
    const espActThing = await WoT.produce({
        title: "actuator",
        description: "Actuator node of MuseumGuard",
        properties: {
            ambientLightBrightness: {
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
                enum: [
                    "IDLE",
                    "IMPACT",
                    "THEFT"
                ],
                default: "IDLE",
            },
        },
        actions: {
            regulateBrightness: {
                description: "Modify artwork illumination level",
                input: {
                    type: "integer",
                    minimum: 0,
                    maximum: 100
                }
            },
            // 20 seconds blink are fixed by specs
            triggerImpactBlink: {
                description: "Blink alarm LED for 20s (impact response)"
            },
            // no added input required
            triggerTheftAlarm: {
                description: "Turn alarm LED permanently on (theft response)"
            },
            resetAlarmLight: {
                description: "Turns off the Impact/Theft Warning LED"
            },
        }
    })


    // PROPERTIES
    espActThing.setPropertyReadHandler("ambientLightBrightness", async () => {
        const state = await getActuatorState();
        return state.brightness;
    });

    espActThing.setPropertyReadHandler("alarmLightState", async () => {
        const state = await getActuatorState();
        return state.alarmState;
    });

    espActThing.setActionHandler("regulateBrightness", async (params) => {
        const brightness = await params.value();
        await setBrightness(brightness);
        return;
    });

    espActThing.setActionHandler("triggerImpactBlink", async () => {
        await startBlink();
        espActThing.emitPropertyChange("alarmLightState");
    });

    espActThing.setActionHandler("triggerTheftAlarm", async () => {
        await activateAlarm();
        espActThing.emitPropertyChange("alarmLightState");
    });

    espActThing.setActionHandler("resetAlarmLight", async () => {
        await resetAlarms();
        espActThing.emitPropertyChange("alarmLightState");
    });


    await espActThing.expose();
    return espActThing;
}
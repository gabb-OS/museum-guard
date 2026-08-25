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
                // NOTA: lo schema accetta "number" (non "integer") perche'
                // node-wot valida l'input contro la TD PRIMA di invocare
                // l'action handler: se qui restasse "integer", un float
                // (es. il predictive-light service che restituisce 80.49)
                // verrebbe scartato con un errore di validazione ancora
                // prima di entrare nell'handler sotto, che quindi non
                // avrebbe mai la possibilita' di correggerlo.
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

    // 1. Cache dello stato per evitare che un fallimento di rete blocchi la readProperty
    let cachedState = { brightness: 0, alarmState: "IDLE" };

    // 2. Polling periodico dello stato in background (con try/catch, come espSenThing)
    setInterval(async () => {
        try {
            const state = await getActuatorState();
            cachedState = state;
            espActThing.emitPropertyChange("artworkLedBrightness");
            espActThing.emitPropertyChange("alarmLightState");
        } catch (err) {
            console.warn("[ESP_ACT] errore poll state:", err.message);
        }
    }, 2000);

    // 3. PROPERTIES con fallback sicuro (leggono dalla cache, NON fanno chiamate di rete sincrone)
    espActThing.setPropertyReadHandler("artworkLedBrightness", async () => {
        return cachedState.brightness;
    });

    espActThing.setPropertyReadHandler("alarmLightState", async () => {
        return cachedState.alarmState;
    });

    // 4. ACTIONS
    espActThing.setActionHandler("regulateBrightness", async (params) => {
        const rawValue = await params.value();

        // Normalizzazione centralizzata: qualunque chiamante (predittivo,
        // fallback reattivo, futuri client WoT) puo' mandare un float o un
        // valore leggermente fuori range senza doversene preoccupare, e'
        // regulateBrightness stesso a decidere il valore fisico corretto.
        const brightness = Math.round(Math.min(100, Math.max(0, rawValue)));

        await setBrightness(brightness);
        cachedState.brightness = brightness; // Aggiorna cache per coerenza immediata
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
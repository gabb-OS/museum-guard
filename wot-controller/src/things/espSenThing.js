import {
    getLightLevel, getAccelReading, subscribeToAlarmEvents,
    getThresholds, setImpactThreshold, setTheftThreshold, resetTracking
} from "../adapters/espSen-adapter.js";

export async function createEspSenTD(WoT) {
    const espSenThing = await WoT.produce({
        title: "sensor",
        description: "Sensor node of MuseumGuard",
        properties: {
            ambientLight: { type: "number", description: "Percentuale di luce rilevata (0-100)", observable: true, readOnly: true },
            accelerometer: {
                type: "object",
                description: "Accelerazione sui tre assi (g)",
                properties: { ax: { type: "number" }, ay: { type: "number" }, az: { type: "number" } },
                observable: true, readOnly: true
            },
            thresholds: {
                type: "object",
                description: "Soglie correnti di rilevamento urto/furto",
                properties: {
                    impact: { type: "number" },
                    theft_displacement: { type: "number" }
                },
                observable: true, readOnly: true
            }
        },
        events: {
            alarmEvent: {
                description: "Notifica impact/theft/position dal sensore",
                data: {
                    type: "object",
                    properties: {
                        type: { type: "string", enum: ["impact", "theft", "position"] },
                        axis: { type: "string" },
                        value: { type: "number" },
                        lat: { type: "number" },
                        lon: { type: "number" }
                    }
                }
            }
        },
        actions: {
            setImpactThreshold: {
                description: "Configura la soglia di rilevamento urto accidentale (asse X)",
                input: { type: "number" }
            },
            setTheftThreshold: {
                description: "Configura la soglia di spostamento verticale per il furto (asse Z)",
                input: { type: "number" }
            },
            resetTracking: {
                description: "Ricalibra la baseline e ferma il tracking GPS dopo un furto (PUT /reset_alarm)"
            }
        },
    });

    let ambientLight = 0;
    setInterval(async () => {
        try {
            ambientLight = await getLightLevel();
            espSenThing.emitPropertyChange("ambientLight");
        } catch (err) { console.warn("[ESP_SEN] errore poll light:", err.message); }
    }, 2000);
    espSenThing.setPropertyReadHandler("ambientLight", async () => ambientLight);

    let accel = { ax: 0, ay: 0, az: 0 };
    setInterval(async () => {
        try {
            accel = await getAccelReading();
            espSenThing.emitPropertyChange("accelerometer");
        } catch (err) { console.warn("[ESP_SEN] errore poll accelerometro:", err.message); }
    }, 2000);
    espSenThing.setPropertyReadHandler("accelerometer", async () => accel);

    let thresholds = { impact: 0, theft_displacement: 0 };
    setInterval(async () => {
        try {
            thresholds = await getThresholds();
            espSenThing.emitPropertyChange("thresholds");
        } catch (err) { console.warn("[ESP_SEN] errore poll thresholds:", err.message); }
    }, 5000);
    espSenThing.setPropertyReadHandler("thresholds", async () => thresholds);

    espSenThing.setActionHandler("setImpactThreshold", async (params) => {
        const value = await params.value();
        await setImpactThreshold(value);
        thresholds = await getThresholds();
        espSenThing.emitPropertyChange("thresholds");
        return undefined;
    });

    espSenThing.setActionHandler("setTheftThreshold", async (params) => {
        const value = await params.value();
        await setTheftThreshold(value);
        thresholds = await getThresholds();
        espSenThing.emitPropertyChange("thresholds");
        return undefined;
    });

    espSenThing.setActionHandler("resetTracking", async () => {
        await resetTracking();
        return undefined;
    });

    subscribeToAlarmEvents(
        (evt) => {
            // Il mock puo' accumulare piu' eventi tra una notifica CoAP e
            // l'altra e inviarli come array: lo schema TD di alarmEvent si
            // aspetta pero' un oggetto singolo, quindi li emettiamo uno a uno.
            const events = Array.isArray(evt) ? evt : [evt];
            events.forEach((e) => espSenThing.emitEvent("alarmEvent", e));
        },
        (err) => console.warn("[ESP_SEN] errore observe /events:", err.message)
    );

    await espSenThing.expose();
    return espSenThing;
}
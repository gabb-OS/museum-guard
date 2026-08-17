// Export a function that takes the WoT object as an argument
module.exports.createEspActTD = async function(WoT) {

    const espActThing = await WoT.produce({
        title: "ESP_ACT",
        description: "Actuator node of MuseumGuard",
        properties: {
            ambientLightBrightness: {
                type: "integer",
                description: "Artwork illumination LED level",
                observable: true,
                readOnly: true
            },
            alarmLightState: {
                type: "boolean",
                description: "Impact/Theft Warning LED state",
                observable: true,
                readOnly: true
            },
        },
        actions: {
            setBrightness: {
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

    // TODO: piu intelligentemente queste potrebbero restituire con un valore ritornato direttamente dall'ESP
    // anziche da una var locale
    let ambientLightBrightness = 50;
    let alarmLightState = false;
    espActThing.setPropertyReadHandler("ambientLightBrightness", async () => currentLightLevel);
    espActThing.setPropertyReadHandler("alarmLightState", async () => alarmLightState);


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



    await espActThing.expose();
    return espActThing;
}
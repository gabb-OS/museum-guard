const { CoapClient } = require("./coap-client");

module.exports.createEspSenTD = async function(WoT) {

    const espSenThing = await WoT.produce({
        title: "ESP_SEN",
        description: "Sensor node of MuseumGuard",
        properties: {
            ambientLight: {
                type: "number",
                description: "Percentuale di luce rilevata (0-100)",
                readOnly: true,
                observable: true
            },
            accelerometer: {
                type: "object",
                description: "Accelerazione sui tre assi (g)",
                properties: {
                    ax: { type: "number" },
                    ay: { type: "number" },
                    az: { type: "number" }
                },
                readOnly: true,
                observable: true
            }
        }
    });

    //light reading stuff
    const sensorCoap = new CoapClient(process.env.ESP_SEN_ADDRESS, process.env.COAP_PORT || 5683);
    let ambientLight = 0;
    setInterval(async () => {
        try {
            const raw = await sensorCoap.get("/light");
            ambientLight = parseFloat(raw);
            espSenThing.emitPropertyChange("ambientLight");
        } catch (err) {
            console.warn("[ESP_SEN] errore poll /light:", err.message);
        }
    }, 2000);

    espSenThing.setPropertyReadHandler("ambientLight", async () => ambientLight);

    //accelerometer reading stuff
    let ax = 0, ay = 0, az = 0;
    setInterval(async () => {
        try {
            const raw = await sensorCoap.get("/accel");
            ({ ax, ay, az } = JSON.parse(raw));
            espSenThing.emitPropertyChange("accelerometer");
        } catch (err) {
            console.warn("[ESP_SEN] errore poll /accel:", err.message);
        }
    }, 2000);

    espSenThing.setPropertyReadHandler("accelerometer", async () => ({ ax, ay, az }));

    await espSenThing.expose();
    return espSenThing;
}
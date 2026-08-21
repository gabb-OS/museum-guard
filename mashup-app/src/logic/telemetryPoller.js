/*

GABE: farei un unico polling loop per leggere
i valori dei sensori e lo stato degli attuatori, POI da scrivere in influx,


- periodically read the sensor measurements from ESP-SEN;
    – ambient light measurements;
    – acceleration values on X/Y/Z axes;

– actuator states (IDLE IMPACT THEFT ???);
– lighting control values (led intensity value ???).
*/

//import { writeTelemetry } from "../services/influxService.js";
import { config } from "../config.js";

export function startTelemetryPolling(sensor, actuator) {
    setInterval(async () => {
        try {
            
            // Sensor
            const lightSens = await (await sensor.readProperty("ambientLight")).value();
            const accelSens = await (await sensor.readProperty("accelerometer")).value();

            // Actuator
            const alarmState = await (await actuator.readProperty("alarmLightState")).value();
            const artworkBrightness = await (await actuator.readProperty("artworkLedBrightness")).value();

            //await writeTelemetry({ lightSens, accelSens, alarmState, artworkBrightness });

        } catch (err) {
            console.warn("[TELEMETRY] errore poll:", err.message);
        }
    }, config.telemetryPollMs);
}
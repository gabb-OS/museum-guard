import { InfluxDB, Point } from "@influxdata/influxdb-client";

const url = process.env.INFLUXDB_URL || "http://influxdb:8086";
const token = process.env.INFLUXDB_TOKEN;
const org = process.env.INFLUXDB_ORG;
const bucket = process.env.INFLUXDB_BUCKET;

if (!token || !org || !bucket) {
    console.warn("[influxService] variabili INFLUXDB_TOKEN/ORG/BUCKET mancanti: i punti non verranno scritti.");
}

const writeApi = new InfluxDB({ url, token }).getWriteApi(org, bucket, "ms", {
    writeFailed: (error, lines, attempt) => {
        console.error(`[influxService] SCRITTURA FALLITA (tentativo ${attempt}):`, error.message);
        console.error("[influxService] righe scartate:", lines);
    }
});
writeApi.useDefaultTags({ system: "museumguard" });

function safeWrite(point) {
    try {
        writeApi.writePoint(point);
    } catch (err) {
        console.error("[influxService] errore scrittura:", err.message);
    }
}

/**
 * Scrive un ciclo di telemetria completo: misure sensore + stato attuatore.
 * data = { lightSens, accelSens: {ax,ay,az}, alarmState, artworkBrightness }
 */
export async function writeTelemetry({ lightSens, accelSens, alarmState, artworkBrightness }) {
    safeWrite(new Point("ambient_light").floatField("value", lightSens));

    safeWrite(
        new Point("acceleration")
            .floatField("ax", accelSens.ax)
            .floatField("ay", accelSens.ay)
            .floatField("az", accelSens.az)
    );

    // "actuator states" + "lighting control values" richiesti dalla traccia
    safeWrite(
        new Point("actuator_state")
            .stringField("alarm_state", alarmState)
            .intField("brightness", artworkBrightness)
    );
}

/**
 * Scrive un evento impact/theft ricevuto da alarmEvent.
 */
export async function writeEvent(event) {
    const measurement = event.type === "theft" ? "theft_event" : "impact_event";
    safeWrite(
        new Point(measurement)
            .stringField("axis", event.axis ?? "")
            .floatField("value", event.value ?? 0)
    );
}

/**
 * Scrive una posizione GPS ricevuta durante il tracking post-furto
 * (evento alarmEvent con type "position", vedi gps_ping_task nel firmware
 * e gps_task nel mock). Measurement dedicata cosi' Grafana puo' mostrare
 * l'ultima posizione nota con un pannello Geomap/Stat separato dagli eventi
 * impact/theft.
 */
export async function writePosition({ lat, lon }) {
    safeWrite(
        new Point("position")
            .floatField("lat", lat)
            .floatField("lon", lon)
    );
}

export async function closeInflux() {
    await writeApi.close();
}
import { InfluxDB, Point } from "@influxdata/influxdb-client";

const url = process.env.INFLUXDB_URL || "http://influxdb:8086";
const token = process.env.INFLUXDB_TOKEN;
const org = process.env.INFLUXDB_ORG;
const bucket = process.env.INFLUXDB_BUCKET;

if (!token || !org || !bucket) {
    console.warn("[influxService] Missing INFLUXDB_TOKEN/ORG/BUCKET: points will not be written.");
}

const writeApi = new InfluxDB({ url, token }).getWriteApi(org, bucket, "ms", {
    flushInterval: 1000,   // Flush every 1s instead of 60s
    writeFailed: (error, lines, attempt) => {
        console.error(`[influxService] WRITE FAILED (attempt ${attempt}):`, error.message);
        console.error("[influxService] discarded lines:", lines);
    }
});
writeApi.useDefaultTags({ system: "museumguard" });

function safeWrite(point) {
    try {
        writeApi.writePoint(point);
    } catch (err) {
        console.error("[influxService] write error:", err.message);
    }
}

/**
 * Writes a full telemetry cycle: sensor measurements + actuator state.
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

    // Actuator states and lighting control values required by specs
    safeWrite(
        new Point("actuator_state")
            .stringField("alarm_state", alarmState)
            .intField("brightness", artworkBrightness)
    );
}

/**
 * Writes an impact/theft event.
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
 * Writes current thresholds so Grafana can display them without querying the WoT on every refresh.
 */
export async function writeThresholds({ impact, theft_displacement }) {
    safeWrite(
        new Point("device_thresholds")
            .floatField("impact", impact)
            .floatField("theft_displacement", theft_displacement)
    );
}

/**
 * Writes GPS position during post-theft tracking. 
 * Dedicated measurement for Grafana Geomap/Stat panels, separate from impact/theft events.
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
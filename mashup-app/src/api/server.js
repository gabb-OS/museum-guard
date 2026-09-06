import express from "express";
import { config } from "../config.js";
import { stopLiveLocation } from "../services/telegramService.js";

/**
 * Starts the mashup-app HTTP server.
 * @param {object} sensor - Sensor WoT Thing, already consumed in index.js
 * @param {object} actuator - Actuator WoT Thing, already consumed in index.js
 * @param {number} port - Listening port (default 3001)
 */
export function startApiServer(sensor, actuator, port = config.expressSrv) {
    const app = express();
    app.use(express.json());

    app.use((req, res, next) => {
        res.header("Access-Control-Allow-Origin", "*");
        res.header("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
        res.header("Access-Control-Allow-Headers", "Content-Type");
        if (req.method === "OPTIONS") {
            return res.sendStatus(204);
        }
        next();
    });

    app.post("/api/resetalarm", async (req, res) => {
        try {
            console.log("[API] Alarm reset request received");

            // Reset in parallel: alarm LED (actuator) + tracking/baseline (sensor)
            const [actuatorResult, sensorResult] = await Promise.allSettled([
                actuator.invokeAction("resetAlarmLight"),
                sensor.invokeAction("resetTracking"),
            ]);

            const errors = [actuatorResult, sensorResult]
                .filter(r => r.status === "rejected")
                .map(r => r.reason?.message ?? String(r.reason));

            if (errors.length > 0) {
                console.error("[API] Partial reset, errors:", errors);
                return res.status(502).json({
                    status: "partial_error",
                    message: "Reset not completed on all devices",
                    errors,
                });
            }
            stopLiveLocation();
            res.json({ status: "ok", message: "Alarm reset triggered" });
        } catch (err) {
            console.error("[API] Error during reset:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // GET /api/thresholds: reads current thresholds from the sensor Thing, 
    // used by the Grafana form to pre-fill values on load.
    app.get("/api/thresholds", async (req, res) => {
        try {
            const thresholds = await (await sensor.readProperty("thresholds")).value();
            res.json(thresholds); // { impact, theft_displacement }
        } catch (err) {
            console.error("[API] Error reading thresholds:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // POST /api/thresholds/impact  body: { "value": <number> }
    app.post("/api/thresholds/impact", async (req, res) => {
        const { value } = req.body;
        if (typeof value !== "number" || Number.isNaN(value)) {
            return res.status(400).json({ status: "error", message: "Missing numeric 'value' field" });
        }
        try {
            console.log(`[API] Set impact threshold = ${value}`);
            await sensor.invokeAction("setImpactThreshold", value);
            res.json({ status: "ok", message: "Impact threshold updated" });
        } catch (err) {
            console.error("[API] Error setting impact threshold:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // POST /api/thresholds/theft  body: { "value": <number> }
    app.post("/api/thresholds/theft", async (req, res) => {
        const { value } = req.body;
        if (typeof value !== "number" || Number.isNaN(value)) {
            return res.status(400).json({ status: "error", message: "Missing numeric 'value' field" });
        }
        try {
            console.log(`[API] Set theft threshold = ${value}`);
            await sensor.invokeAction("setTheftThreshold", value);
            res.json({ status: "ok", message: "Theft threshold updated" });
        } catch (err) {
            console.error("[API] Error setting theft threshold:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // Health check
    app.get("/api/health", (req, res) => {
        res.json({ status: "ok" });
    });

    app.listen(port, () => {
        console.log(`[API] Mashup API server listening on port ${port}`);
    });
}
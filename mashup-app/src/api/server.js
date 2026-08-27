import express from "express";
import {config} from "../config.js";


/**
 * Avvia il server HTTP del mashup-app.
 * @param {object} sensor - Thing WoT del sensore, gia' consumato in index.js
 * @param {object} actuator - Thing WoT dell'attuatore, gia' consumato in index.js
 * @param {number} port - porta di ascolto (default 3001)
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
            console.log("[API] Richiesta reset allarme ricevuta");
            await actuator.invokeAction("resetAlarmLight");
            res.json({ status: "ok", message: "Alarm reset triggered" });
        } catch (err) {
            console.error("[API] Errore durante il reset:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // GET /api/thresholds: legge le soglie correnti dal sensor Thing,
    // usato dal form Grafana per precompilare il valore all'apertura.
    app.get("/api/thresholds", async (req, res) => {
        try {
            const thresholds = await (await sensor.readProperty("thresholds")).value();
            res.json(thresholds); // { impact, theft_displacement }
        } catch (err) {
            console.error("[API] Errore lettura thresholds:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // POST /api/thresholds/impact  body: { "value": <number> }
    app.post("/api/thresholds/impact", async (req, res) => {
        const { value } = req.body;
        if (typeof value !== "number" || Number.isNaN(value)) {
            return res.status(400).json({ status: "error", message: "campo 'value' numerico mancante" });
        }
        try {
            console.log(`[API] Set impact threshold = ${value}`);
            await sensor.invokeAction("setImpactThreshold", value);
            res.json({ status: "ok", message: "Impact threshold updated" });
        } catch (err) {
            console.error("[API] Errore set impact threshold:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // POST /api/thresholds/theft  body: { "value": <number> }
    app.post("/api/thresholds/theft", async (req, res) => {
        const { value } = req.body;
        if (typeof value !== "number" || Number.isNaN(value)) {
            return res.status(400).json({ status: "error", message: "campo 'value' numerico mancante" });
        }
        try {
            console.log(`[API] Set theft threshold = ${value}`);
            await sensor.invokeAction("setTheftThreshold", value);
            res.json({ status: "ok", message: "Theft threshold updated" });
        } catch (err) {
            console.error("[API] Errore set theft threshold:", err.message);
            res.status(502).json({ status: "error", message: err.message });
        }
    });

    // Health check
    app.get("/api/health", (req, res) => {
        res.json({ status: "ok" });
    });

    app.listen(port, () => {
        console.log(`[API] Mashup API server in ascolto sulla porta ${port}`);
    });
}
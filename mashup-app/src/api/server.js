import express from "express";
import cors from "cors";
import {config} from "../config.js";


/**
 * Avvia il server HTTP del mashup-app.
 * @param {object} actuator - Thing WoT dell'attuatore, gia' consumato in index.js
 * @param {number} port - porta di ascolto (default 3001)
 */
export function startApiServer(actuator, port = config.expressSrv) {
    const app = express();
    //app.use(cors()); 
    app.use(express.json());

    app.use((req, res, next) => {
        res.header("Access-Control-Allow-Origin", "*");
        res.header("Access-Control-Allow-Methods", "POST, OPTIONS");
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

    // Health check
    app.get("/api/health", (req, res) => {
        res.json({ status: "ok" });
    });

    app.listen(port, () => {
        console.log(`[API] Mashup API server in ascolto sulla porta ${port}`);
    });
}
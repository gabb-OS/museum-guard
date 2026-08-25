"""
Predictive Light Service.

Microservizio FastAPI che espone GET /predict (ritorna {"brightness": <float>})
e GET /health. Legge la serie storica ambient_light da InfluxDB, rifitta un
modello ARIMA su finestra rolling ogni PREDICT_INTERVAL_S secondi, applica
una bias correction adattiva (EMA degli errori di previsione passati) e
scrive su InfluxDB sia le predizioni (measurement: predicted_light) sia gli
errori di riconciliazione (measurement: prediction_error).

Design:
  - /predict è sempre veloce: ritorna l'ultimo valore già calcolato dal
    background loop. Il fit pesante NON avviene in risposta alla HTTP.
  - Un background task periodico:
      1) Riconcilia le previsioni scadute (confronta predicted vs actual)
      2) Aggiorna la EMA dell'errore (bias correction)
      3) Rifitta ARIMA sulla finestra storica corrente
      4) Predice a PREDICTION_HORIZON_S nel futuro
      5) Applica la correzione e scrive su InfluxDB
"""
import os
import time
import math
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import pmdarima as pm
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("predictive-light")

# ----------------------------- Config ---------------------------------
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "museumguard")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "museumguard")

PREDICT_INTERVAL_S = int(os.environ.get("PREDICT_INTERVAL_S", "5"))
PREDICTION_HORIZON_S = int(os.environ.get("PREDICTION_HORIZON_S", "30"))
HISTORY_WINDOW_S = int(os.environ.get("HISTORY_WINDOW_S", "600"))
EMA_ALPHA = float(os.environ.get("EMA_ALPHA", "0.3"))
MIN_SAMPLES_TO_FIT = int(os.environ.get("MIN_SAMPLES_TO_FIT", "20"))

# ----------------------------- InfluxDB -------------------------------
influx = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
write_api = influx.write_api(write_options=SYNCHRONOUS)
query_api = influx.query_api()


def read_ambient_light(window_s: int) -> list[tuple[datetime, float]]:
    """Legge gli ultimi window_s secondi di ambient_light da InfluxDB."""
    since = datetime.now(timezone.utc) - timedelta(seconds=window_s)
    flux = f"""
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: {since.isoformat()})
      |> filter(fn: (r) => r._measurement == "ambient_light" and r._field == "value")
      |> sort(columns: ["_time"], desc: false)
    """
    try:
        tables = query_api.query(flux)
    except Exception as exc:
        logger.warning("InfluxDB query failed: %s", exc)
        return []

    series: list[tuple[datetime, float]] = []
    for table in tables:
        for rec in table.records:
            series.append((rec.get_time(), float(rec.get_value())))
    return series


def read_unreconciled_predictions() -> list[dict]:
    """Legge le predizioni non ancora riconciliate (target_timestamp <= now)."""
    now = datetime.now(timezone.utc)
    # Cerchiamo previsioni con target_timestamp passato ma senza errore ancora scritto
    flux = f"""
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -{HISTORY_WINDOW_S}s)
      |> filter(fn: (r) => r._measurement == "predicted_light" and r._field == "predicted_value")
      |> filter(fn: (r) => exists r.target_timestamp)
      |> sort(columns: ["_time"], desc: false)
    """
    try:
        tables = query_api.query(flux)
    except Exception as exc:
        logger.warning("InfluxDB predictions query failed: %s", exc)
        return []

    preds = []
    for table in tables:
        for rec in table.records:
            target_ts_str = rec.values.get("target_timestamp")
            if target_ts_str is None:
                continue
            try:
                target_ts = datetime.fromisoformat(str(target_ts_str).replace("Z", "+00:00"))
            except ValueError:
                continue
            if target_ts <= now:
                preds.append({
                    "time": rec.get_time(),
                    "predicted_value": float(rec.get_value()),
                    "target_timestamp": target_ts,
                    "horizon_s": float(rec.values.get("horizon_s", PREDICTION_HORIZON_S)),
                })
    return preds


def read_actual_at(target_ts: datetime, tolerance_s: int = 15) -> float | None:
    """Legge il valore reale di ambient_light più vicino a target_ts (±tolerance_s)."""
    t0 = target_ts - timedelta(seconds=tolerance_s)
    t1 = target_ts + timedelta(seconds=tolerance_s)
    flux = f"""
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: {t0.isoformat()}, stop: {t1.isoformat()})
      |> filter(fn: (r) => r._measurement == "ambient_light" and r._field == "value")
      |> sort(columns: ["_time"], desc: false)
      |> last()
    """
    try:
        tables = query_api.query(flux)
    except Exception as exc:
        logger.warning("InfluxDB actual query failed: %s", exc)
        return None

    for table in tables:
        for rec in table.records:
            return float(rec.get_value())
    return None


def write_prediction(predicted_value: float, target_timestamp: datetime, horizon_s: int):
    p = Point("predicted_light") \
        .field("predicted_value", predicted_value) \
        .field("horizon_s", horizon_s) \
        .field("target_timestamp", target_timestamp.isoformat()) \
        .time(datetime.now(timezone.utc), WritePrecision.MS)
    write_api.write(bucket=INFLUXDB_BUCKET, record=p)


def write_prediction_error(predicted_value: float, actual_value: float, target_ts: datetime):
    err = actual_value - predicted_value
    p = Point("prediction_error") \
        .field("error", err) \
        .field("abs_error", abs(err)) \
        .field("predicted", predicted_value) \
        .field("actual", actual_value) \
        .time(target_ts, WritePrecision.MS)
    write_api.write(bucket=INFLUXDB_BUCKET, record=p)


# ----------------------------- Model ----------------------------------
class Predictor:
    """ARIMA con bias correction adattiva (EMA sull'errore)."""

    def __init__(self):
        self.last_brightness: float | None = None
        self.last_order: tuple | None = None
        self.ema_error: float = 0.0  # bias corrente
        self.last_fit_ts: datetime | None = None

    def update_bias(self, new_error: float):
        # EMA: e_t = α·e_new + (1-α)·e_{t-1}
        self.ema_error = EMA_ALPHA * new_error + (1 - EMA_ALPHA) * self.ema_error

    def fit_and_predict(self, series: list[tuple[datetime, float]]) -> float | None:
        if len(series) < MIN_SAMPLES_TO_FIT:
            logger.info("Not enough samples yet (%d/%d)", len(series), MIN_SAMPLES_TO_FIT)
            return None

        values = np.array([v for _, v in series], dtype=float)

        # Refit ARIMA su finestra rolling (adattamento al trend corrente)
        try:
            model = pm.auto_arima(
                values,
                seasonal=False,
                suppress_warnings=True,
                error_action="ignore",
                stepwise=True,
                max_order=3,
            )
        except Exception as exc:
            logger.error("ARIMA fit failed: %s", exc)
            return None

        # Stima del passo temporale medio della serie (secondi)
        times = [t.timestamp() for t, _ in series]
        dt = (times[-1] - times[0]) / max(1, len(times) - 1)
        steps_ahead = max(1, int(round(PREDICTION_HORIZON_S / max(dt, 1e-3))))

        try:
            forecast, _ = model.predict(n_periods=steps_ahead, return_conf_int=True)
            raw_value = float(forecast[-1])
        except Exception as exc:
            logger.error("ARIMA predict failed: %s", exc)
            return None

        # Bias correction adattiva
        corrected = raw_value + self.ema_error
        # Clamp a [0, 100] (è una percentuale di luminosità)
        corrected = float(max(0.0, min(100.0, corrected)))

        self.last_brightness = corrected
        self.last_order = tuple(model.order)
        self.last_fit_ts = datetime.now(timezone.utc)
        logger.info(
            "Fit OK order=%s steps_ahead=%d raw=%.2f bias=%.2f corrected=%.2f (window=%d)",
            model.order, steps_ahead, raw_value, self.ema_error, corrected, len(values),
        )
        return corrected


predictor = Predictor()

# ----------------------------- Background loop ------------------------
async def predict_loop():
    """Job periodico: riconcilia previsioni scadute + rifitta ARIMA."""
    # Piccolo delay iniziale per lasciare il tempo a InfluxDB di raccogliere dati
    await asyncio.sleep(5)
    while True:
        try:
            # 1) Riconciliazione: previsioni scadute → errore → aggiorna EMA
            preds = read_unreconciled_predictions()
            for p in preds:
                actual = read_actual_at(p["target_timestamp"])
                if actual is None:
                    continue  # dato reale non ancora arrivato, riproveremo
                err = actual - p["predicted_value"]
                predictor.update_bias(err)
                write_prediction_error(p["predicted_value"], actual, p["target_timestamp"])
                logger.info(
                    "Reconciled pred@%s: predicted=%.2f actual=%.2f err=%.2f ema_bias=%.2f",
                    p["target_timestamp"].isoformat(), p["predicted_value"], actual, err, predictor.ema_error,
                )

            # 2) Refit + previsione
            series = read_ambient_light(HISTORY_WINDOW_S)
            value = predictor.fit_and_predict(series)
            if value is not None:
                target_ts = datetime.now(timezone.utc) + timedelta(seconds=PREDICTION_HORIZON_S)
                write_prediction(value, target_ts, PREDICTION_HORIZON_S)
        except Exception as exc:
            logger.exception("predict_loop error: %s", exc)

        await asyncio.sleep(PREDICT_INTERVAL_S)


# ----------------------------- FastAPI --------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(predict_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        influx.close()


app = FastAPI(title="Predictive Light Service", lifespan=lifespan)


@app.get("/predict")
async def predict():
    if predictor.last_brightness is None:
        # A freddo, prima che il loop abbia fatto un fit: 503 così il
        # mashup usa il fallback reattivo (computeTargetBrightness).
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"error": "no prediction available yet"},
        )
    return {"brightness": predictor.last_brightness}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "last_brightness": predictor.last_brightness,
        "last_order": list(predictor.last_order) if predictor.last_order else None,
        "ema_bias": predictor.ema_error,
        "last_fit_ts": predictor.last_fit_ts.isoformat() if predictor.last_fit_ts else None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
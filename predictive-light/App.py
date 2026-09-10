"""
Predictive Light Service (FastAPI).
Exposes GET /predict (returns {"predicted_ambient_light": <float>}) and GET /health.

Design: "refit-once-serve-many"
- Every PREDICT_INTERVAL_S (default 60s), ARIMA refits on a rolling window of ambient_light and generates a forecast cache (one point every FORECAST_STEP_S).
- GET /predict never triggers a fit. It reads the closest cached point based on elapsed time.
- Bias correction (EMA) and reconciliation run on a single point per refit cycle at PREDICTION_HORIZON_S.

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

# Refit interval for ARIMA and forecast cache regeneration. Default 60s.
PREDICT_INTERVAL_S = int(os.environ.get("PREDICT_INTERVAL_S", "60"))
# Forecast cache granularity (seconds). Aligned with ambient_light telemetry polling resolution.
FORECAST_STEP_S = int(os.environ.get("FORECAST_STEP_S", "1"))
# Prediction horizon for reconciliation and bias correction (seconds). Default 30s.
PREDICTION_HORIZON_S = int(os.environ.get("PREDICTION_HORIZON_S", "30"))
# History window (i.e. last N seconds of ambient_light data) for ARIMA fit (seconds). Default 600s (10min).
HISTORY_WINDOW_S = int(os.environ.get("HISTORY_WINDOW_S", "600"))
EMA_ALPHA = float(os.environ.get("EMA_ALPHA", "0.3"))
MIN_SAMPLES_TO_FIT = int(os.environ.get("MIN_SAMPLES_TO_FIT", "20"))

# ----------------------------- InfluxDB -------------------------------
influx = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
write_api = influx.write_api(write_options=SYNCHRONOUS)
query_api = influx.query_api()

# Default tag matching mashup (influxService.js) for consistency.
DEFAULT_TAGS = {"system": "museumguard"}

# Track reconciled targets to avoid updating EMA multiple times for the same prediction
reconciled_targets: set[str] = set()

def load_reconciled_targets() -> set[str]:
    """Load already reconciled targets from InfluxDB to avoid duplicates on restart."""
    flux = f"""
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -{HISTORY_WINDOW_S}s)
      |> filter(fn: (r) => r._measurement == "prediction_error")
    """
    try:
        tables = query_api.query(flux)
    except Exception as exc:
        logger.warning("Failed to load reconciled targets: %s", exc)
        return set()

    targets = set()
    for table in tables:
        for rec in table.records:
            targets.add(rec.get_time().isoformat())
    return targets

def read_ambient_light(window_s: int) -> list[tuple[datetime, float]]:
    """Read the last window_s seconds of ambient_light from InfluxDB."""
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
    """Read unreconciled predictions (target_timestamp <= now)."""
    now = datetime.now(timezone.utc)

    # InfluxDB stores fields in separate rows. pivot() combines predicted_value, target_timestamp, and horizon_s.
    flux = f"""
    from(bucket: "{INFLUXDB_BUCKET}")
      |> range(start: -{HISTORY_WINDOW_S}s)
      |> filter(fn: (r) => r._measurement == "predicted_light")
      |> filter(fn: (r) => r._field == "predicted_value" or r._field == "target_timestamp" or r._field == "horizon_s")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
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
                ts_str = str(target_ts_str)
                if ts_str.endswith("Z"):
                    ts_str = ts_str.replace("Z", "+00:00")
                target_ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue

            if target_ts <= now:
                preds.append({
                    "time": rec.get_time(),
                    "predicted_value": float(rec.values.get("predicted_value")),
                    "target_timestamp": target_ts,
                    "horizon_s": float(rec.values.get("horizon_s", PREDICTION_HORIZON_S)),
                })
    return preds

def read_actual_at(target_ts: datetime, tolerance_s: int = 15) -> float | None:
    """Read the actual ambient_light value closest to target_ts (±tolerance_s)."""
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
        .tag("system", DEFAULT_TAGS["system"]) \
        .field("predicted_value", predicted_value) \
        .field("horizon_s", horizon_s) \
        .field("target_timestamp", target_timestamp.isoformat()) \
        .time(datetime.now(timezone.utc), WritePrecision.MS)
    write_api.write(bucket=INFLUXDB_BUCKET, record=p)

def write_prediction_error(predicted_value: float, actual_value: float, target_ts: datetime):
    err = actual_value - predicted_value
    p = Point("prediction_error") \
        .tag("system", DEFAULT_TAGS["system"]) \
        .field("error", err) \
        .field("abs_error", abs(err)) \
        .field("predicted", predicted_value) \
        .field("actual", actual_value) \
        .time(target_ts, WritePrecision.MS)
    write_api.write(bucket=INFLUXDB_BUCKET, record=p)

# ----------------------------- Model ----------------------------------
class Predictor:
    """
    ARIMA with adaptive bias correction (EMA) and forecast cache.
    fit() generates a forecast cache covering the interval until the next refit.
    current_ambient_forecast() reads from this cache based on elapsed time, without calling ARIMA.
    """
    def __init__(self):
        # forecast_cache: list of (target_ts, corrected_ambient_value), ordered by time.
        self.forecast_cache: list[tuple[datetime, float]] = []
        self.cache_fit_ts: datetime | None = None

        self.last_ambient_forecast: float | None = None  # Last served value (% ambient light)
        self.last_order: tuple | None = None
        self.ema_error: float = 0.0  # Current bias
        self.last_fit_ts: datetime | None = None

    def update_bias(self, new_error: float):
        # EMA: e_t = α·e_new + (1-α)·e_{t-1}
        self.ema_error = EMA_ALPHA * new_error + (1 - EMA_ALPHA) * self.ema_error

    def fit(self, series: list[tuple[datetime, float]]) -> tuple[float, datetime] | None:
        """
        Refits ARIMA and rebuilds forecast_cache with one prediction every FORECAST_STEP_S.
        Returns (predicted_value, target_ts) closest to PREDICTION_HORIZON_S for reconciliation.
        Returns None if fit fails.
        """
        if len(series) < MIN_SAMPLES_TO_FIT:
            logger.info("Not enough samples yet (%d/%d)", len(series), MIN_SAMPLES_TO_FIT)
            return None

        values = np.array([v for _, v in series], dtype=float)

        # Refit ARIMA on rolling window to adapt to current trend
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

        # Estimate average time step of historical series (seconds). ARIMA forecasts at the same spacing as input data.
        times = [t.timestamp() for t, _ in series]
        dt = (times[-1] - times[0]) / max(1, len(times) - 1)

        # Steps needed to cover the interval until the next refit
        steps_needed = max(1, int(math.ceil(PREDICT_INTERVAL_S / max(dt, 1e-3))))

        try:
            forecast, _ = model.predict(n_periods=steps_needed, return_conf_int=True)
        except Exception as exc:
            logger.error("ARIMA predict failed: %s", exc)
            return None

        fit_ts = datetime.now(timezone.utc)

        # Rebuild cache: one point every FORECAST_STEP_S seconds, resampled to the regular grid.
        new_cache: list[tuple[datetime, float]] = []
        n_grid_points = max(1, int(math.ceil(PREDICT_INTERVAL_S / FORECAST_STEP_S)))
        for g in range(1, n_grid_points + 1):
            future_s = g * FORECAST_STEP_S
            idx = min(len(forecast) - 1, max(0, int(round(future_s / max(dt, 1e-3))) - 1))
            raw_value = float(forecast[idx])
            corrected = float(max(0.0, min(100.0, raw_value + self.ema_error)))
            target_ts = fit_ts + timedelta(seconds=future_s)
            new_cache.append((target_ts, corrected))

        self.forecast_cache = new_cache
        self.cache_fit_ts = fit_ts
        self.last_order = tuple(model.order)
        self.last_fit_ts = fit_ts

        # Reference point for reconciliation/bias: closest to PREDICTION_HORIZON_S in the future.
        horizon_ts, horizon_value = min(
            new_cache, key=lambda item: abs((item[0] - fit_ts).total_seconds() - PREDICTION_HORIZON_S)
        )

        logger.info(
            "Fit OK order=%s dt=%.2fs steps_needed=%d cache_points=%d bias=%.2f horizon_value=%.2f (window=%d)",
            model.order, dt, steps_needed, len(new_cache), self.ema_error, horizon_value, len(values),
        )

        return horizon_value, horizon_ts

    def current_ambient_forecast(self) -> float | None:
        """
        Returns the cached ambient-light forecast point closest to "now".
        After the first fit, NEVER returns None - always serves the last valid value
        if cache is expired, avoiding gaps in predictions.
        """
        if not self.forecast_cache or self.cache_fit_ts is None:
            return None

        now = datetime.now(timezone.utc)
        elapsed = (now - self.cache_fit_ts).total_seconds()

        # Index on the regular FORECAST_STEP_S grid
        idx = int(round(elapsed / FORECAST_STEP_S)) - 1
        
        # CLAMP: Se siamo fuori dalla cache, usa l'ultimo valore disponibile
        # invece di tornare None (evita gap nel grafico)
        if idx >= len(self.forecast_cache):
            # Cache expired: serve last valid value
            _, value = self.forecast_cache[-1]
        elif idx < 0:
            _, value = self.forecast_cache[0]
        else:
            _, value = self.forecast_cache[idx]

        self.last_ambient_forecast = value
        return value

predictor = Predictor()

# ----------------------------- Background loop ------------------------
def run_refit_cycle():
    """
    Un intero ciclo di reconciliation + refit ARIMA + write.
    Tutta roba SINCRONA e potenzialmente lenta (query Influx, pm.auto_arima,
    write Influx). Viene lanciata dentro un thread via asyncio.to_thread()
    cosi' l'event loop resta libero di rispondere a GET /predict (che legge
    solo dalla cache, in memoria) mentre questo gira.
    """
    # 1) Reconciliation
    preds = read_unreconciled_predictions()
    for p in preds:
        ts_key = p["target_timestamp"].isoformat()
        if ts_key in reconciled_targets:
            continue
        actual = read_actual_at(p["target_timestamp"])
        if actual is None:
            continue
        err = actual - p["predicted_value"]
        predictor.update_bias(err)
        write_prediction_error(p["predicted_value"], actual, p["target_timestamp"])
        reconciled_targets.add(ts_key)
        logger.info(
            "Reconciled pred@%s: predicted=%.2f actual=%.2f err=%.2f ema_bias=%.2f",
            p["target_timestamp"].isoformat(), p["predicted_value"], actual, err, predictor.ema_error,
        )

    # 2) Refit
    series = read_ambient_light(HISTORY_WINDOW_S)
    result = predictor.fit(series)
    if result is not None:
        horizon_value, horizon_ts = result
        write_prediction(horizon_value, horizon_ts, PREDICTION_HORIZON_S)


async def predict_loop():
    """Periodic job: reconcile expired predictions + refit ARIMA."""
    await asyncio.sleep(5)
    
    global reconciled_targets
    reconciled_targets.update(load_reconciled_targets())
    logger.info("Loaded %d already reconciled targets.", len(reconciled_targets))
    
    while True:
        try:
            # Eseguito in un worker thread: non blocca piu' l'event loop,
            # quindi GET /predict resta reattivo durante tutto il refit
            # (query Influx + ARIMA fit + write), niente piu' buchi nella
            # serie servita al polling esterno.
            await asyncio.to_thread(run_refit_cycle)
        except Exception as exc:
            logger.exception("predict_loop error: %s", exc)
        
        # Calcola quando fare il prossimo refit
        # Anticipa il refit di 10 secondi per dare tempo ad ARIMA di completare
        REFIT_ADVANCE_S = 10
        if predictor.cache_fit_ts is not None:
            next_refit_time = predictor.cache_fit_ts + timedelta(seconds=PREDICT_INTERVAL_S - REFIT_ADVANCE_S)
            now = datetime.now(timezone.utc)
            sleep_time = (next_refit_time - now).total_seconds()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(1)  # Fallback minimo
        else:
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
    # No fit here: reads the cached point corresponding to "now", populated by the last periodic refit.
    ambient_forecast = predictor.current_ambient_forecast()
    if ambient_forecast is None:
        # Cold start (before first fit): returns 503 so the mashup uses the reactive fallback.
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"error": "no prediction available yet"},
        )
    return {"predicted_ambient_light": ambient_forecast}

@app.get("/health")
async def health():
    cache_age_s = None
    if predictor.cache_fit_ts is not None:
        cache_age_s = (datetime.now(timezone.utc) - predictor.cache_fit_ts).total_seconds()
    return {
        "status": "ok",
        "last_ambient_forecast": predictor.last_ambient_forecast,
        "last_order": list(predictor.last_order) if predictor.last_order else None,
        "ema_bias": predictor.ema_error,
        "last_fit_ts": predictor.last_fit_ts.isoformat() if predictor.last_fit_ts else None,
        "forecast_cache_points": len(predictor.forecast_cache),
        "forecast_cache_age_s": cache_age_s,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
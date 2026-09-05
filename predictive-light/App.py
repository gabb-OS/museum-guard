"""
Predictive Light Service.
Microservizio FastAPI che espone GET /predict (ritorna {"brightness": <float>})
e GET /health.

Design "refit-once-serve-many":
- Ogni PREDICT_INTERVAL_S secondi (default 60s) il servizio rifitta ARIMA su
  una finestra storica rolling di ambient_light e genera in un colpo solo
  un array di previsioni future, una ogni FORECAST_STEP_S secondi (default 1s),
  fino a coprire l'intervallo prima del prossimo refit.
- Questo array (forecast_cache) resta in memoria. GET /predict NON rifà mai
  un fit: calcola solo quanto tempo è passato dall'ultimo refit e restituisce
  il punto della cache più vicino a "adesso", incrementando quindi l'indice
  ad ogni chiamata senza costo di calcolo aggiuntivo.
- La bias correction (EMA sugli errori passati) e la riconciliazione con il
  valore reale continuano a lavorare su un singolo punto per ciclo di refit,
  preso a PREDICTION_HORIZON_S secondi di distanza dal fit (measurement:
  predicted_light / prediction_error), esattamente come prima.

Nota: il fit, la bias correction e la riconciliazione dell'errore lavorano
sempre nello spazio "luce ambientale prevista" (stessa scala di ambient_light,
comparabile 1:1 nel pannello Grafana dedicato e in predicted_light). Solo il
valore restituito da GET /predict viene convertito nel target di luminosità
artificiale del LED (relazione inversa: brightness = 100 - luce_ambientale_prevista),
perché più luce ambientale c'è meno illuminazione artificiale serve.
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

# PREDICT_INTERVAL_S ora è l'intervallo di REFIT (non più di ogni singola
# predizione): ogni quanto viene rifittato ARIMA e rigenerata la cache di
# previsioni. Alzato a 60s di default proprio perché con "refit-once-serve-many"
# non serve più rifittare ad ogni chiamata di /predict.
PREDICT_INTERVAL_S = int(os.environ.get("PREDICT_INTERVAL_S", "60"))
# Granularità delle previsioni servite dalla cache (secondi tra un punto e il
# successivo). Ha senso tenerlo allineato al polling reale di ambient_light
# (TELEMETRY_POLL_MS nel mashup) così ARIMA prevede alla stessa risoluzione
# con cui riceve i dati storici.
FORECAST_STEP_S = int(os.environ.get("FORECAST_STEP_S", "1"))
PREDICTION_HORIZON_S = int(os.environ.get("PREDICTION_HORIZON_S", "30"))
HISTORY_WINDOW_S = int(os.environ.get("HISTORY_WINDOW_S", "600"))
EMA_ALPHA = float(os.environ.get("EMA_ALPHA", "0.3"))
MIN_SAMPLES_TO_FIT = int(os.environ.get("MIN_SAMPLES_TO_FIT", "20"))

# ----------------------------- InfluxDB -------------------------------
influx = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
write_api = influx.write_api(write_options=SYNCHRONOUS)
query_api = influx.query_api()

# Stesso tag di default usato lato mashup (influxService.js: useDefaultTags),
# cosi' predicted_light/prediction_error sono coerenti col resto dei punti
# scritti nel bucket.
DEFAULT_TAGS = {"system": "museumguard"}

# Track reconciled targets to avoid updating EMA multiple times for the same prediction
reconciled_targets: set[str] = set()

def load_reconciled_targets() -> set[str]:
    """Carica i target già riconciliati da InfluxDB per evitare duplicati al riavvio."""
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
    
    # FIX: InfluxDB stores fields in separate rows. We must use pivot() to combine 
    # predicted_value, target_timestamp, and horizon_s into a single record.
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
    ARIMA con bias correction adattiva (EMA sull'errore) e cache di previsioni.

    Ad ogni fit() viene generato un array di previsioni future (forecast_cache),
    una ogni FORECAST_STEP_S secondi, che copre l'intervallo fino al prossimo
    refit. current_ambient_forecast() legge da questa cache in base al tempo
    trascorso, senza mai richiamare ARIMA.
    """
    def __init__(self):
        # forecast_cache: lista di (target_ts, valore_ambientale_corretto),
        # ordinata per tempo crescente, generata all'ultimo fit.
        self.forecast_cache: list[tuple[datetime, float]] = []
        self.cache_fit_ts: datetime | None = None

        self.last_ambient_forecast: float | None = None  # ultimo valore servito (% luce ambientale)
        self.last_brightness: float | None = None          # ultimo valore servito, convertito in target LED
        self.last_order: tuple | None = None
        self.ema_error: float = 0.0  # bias corrente
        self.last_fit_ts: datetime | None = None

    def update_bias(self, new_error: float):
        # EMA: e_t = α·e_new + (1-α)·e_{t-1}
        self.ema_error = EMA_ALPHA * new_error + (1 - EMA_ALPHA) * self.ema_error

    def fit(self, series: list[tuple[datetime, float]]) -> tuple[float, datetime] | None:
        """
        Rifitta ARIMA e ricostruisce forecast_cache con una previsione ogni
        FORECAST_STEP_S secondi, fino a coprire PREDICT_INTERVAL_S secondi
        (l'intervallo prima del prossimo refit).

        Ritorna (predicted_value, target_ts) del punto più vicino a
        PREDICTION_HORIZON_S secondi nel futuro, da usare per la
        riconciliazione/bias come prima. None se il fit non è possibile.
        """
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

        # Stima del passo temporale medio della serie storica (secondi):
        # ARIMA produce forecast alla stessa spaziatura dei dati in input,
        # quindi idealmente dt ≈ FORECAST_STEP_S (allinea il polling di
        # ambient_light a FORECAST_STEP_S per avere previsioni pulite).
        times = [t.timestamp() for t, _ in series]
        dt = (times[-1] - times[0]) / max(1, len(times) - 1)

        # Quanti passi servono per coprire l'intervallo fino al prossimo refit
        steps_needed = max(1, int(math.ceil(PREDICT_INTERVAL_S / max(dt, 1e-3))))

        try:
            forecast, _ = model.predict(n_periods=steps_needed, return_conf_int=True)
        except Exception as exc:
            logger.error("ARIMA predict failed: %s", exc)
            return None

        fit_ts = datetime.now(timezone.utc)

        # Ricostruisce la cache: un punto ogni FORECAST_STEP_S secondi.
        # forecast[i] corrisponde a "dt*(i+1)" secondi nel futuro rispetto al
        # fit; qui la ricampioniamo sulla griglia regolare FORECAST_STEP_S
        # prendendo, per ogni step della griglia, il punto forecast più vicino.
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

        # Punto di riferimento per la riconciliazione/bias, come prima:
        # quello più vicino a PREDICTION_HORIZON_S secondi nel futuro.
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
        Restituisce il punto della cache più vicino ad "adesso", senza mai
        richiamare ARIMA. Se la cache è vuota (nessun fit ancora riuscito)
        ritorna None. Se "adesso" è oltre l'ultimo punto cachato (refit in
        ritardo), resta agganciato all'ultimo valore disponibile.
        """
        if not self.forecast_cache or self.cache_fit_ts is None:
            return None

        now = datetime.now(timezone.utc)
        elapsed = (now - self.cache_fit_ts).total_seconds()

        # Indice sulla griglia regolare FORECAST_STEP_S, clampato ai bordi
        idx = int(round(elapsed / FORECAST_STEP_S)) - 1
        idx = max(0, min(len(self.forecast_cache) - 1, idx))

        _, value = self.forecast_cache[idx]

        self.last_ambient_forecast = value
        self.last_brightness = float(max(0.0, min(100.0, 100.0 - value)))
        return value

predictor = Predictor()

# ----------------------------- Background loop ------------------------
async def predict_loop():
    """Job periodico: riconcilia previsioni scadute + rifitta ARIMA."""
    # Piccolo delay iniziale per lasciare il tempo a InfluxDB di raccogliere dati
    await asyncio.sleep(5)
    
    # Carica i target già riconciliati per evitare di ricalcolare l'EMA su errori vecchi
    global reconciled_targets
    reconciled_targets.update(load_reconciled_targets())
    logger.info("Loaded %d already reconciled targets.", len(reconciled_targets))
    
    while True:
        try:
            # 1) Riconciliazione: previsioni scadute → errore → aggiorna EMA
            preds = read_unreconciled_predictions()
            for p in preds:
                ts_key = p["target_timestamp"].isoformat()
                if ts_key in reconciled_targets:
                    continue  # Già riconciliata in questa esecuzione o in precedenza
                
                actual = read_actual_at(p["target_timestamp"])
                if actual is None:
                    continue  # dato reale non ancora arrivato, riproveremo
                
                err = actual - p["predicted_value"]
                predictor.update_bias(err)
                write_prediction_error(p["predicted_value"], actual, p["target_timestamp"])
                reconciled_targets.add(ts_key)
                
                logger.info(
                    "Reconciled pred@%s: predicted=%.2f actual=%.2f err=%.2f ema_bias=%.2f",
                    p["target_timestamp"].isoformat(), p["predicted_value"], actual, err, predictor.ema_error,
                )
            
            # 2) Refit: rigenera l'intera cache di previsioni in un colpo solo.
            #    GET /predict, nel frattempo, continua a servire dalla cache
            #    esistente senza mai bloccarsi in attesa di questo fit.
            series = read_ambient_light(HISTORY_WINDOW_S)
            result = predictor.fit(series)
            if result is not None:
                horizon_value, horizon_ts = result
                write_prediction(horizon_value, horizon_ts, PREDICTION_HORIZON_S)
                
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
    # Nessun fit qui dentro: legge solo il punto di cache corrispondente ad
    # "adesso", popolata dall'ultimo refit periodico in predict_loop().
    ambient_forecast = predictor.current_ambient_forecast()
    if ambient_forecast is None:
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
    cache_age_s = None
    if predictor.cache_fit_ts is not None:
        cache_age_s = (datetime.now(timezone.utc) - predictor.cache_fit_ts).total_seconds()
    return {
        "status": "ok",
        "last_ambient_forecast": predictor.last_ambient_forecast,
        "last_brightness": predictor.last_brightness,
        "last_order": list(predictor.last_order) if predictor.last_order else None,
        "ema_bias": predictor.ema_error,
        "last_fit_ts": predictor.last_fit_ts.isoformat() if predictor.last_fit_ts else None,
        "forecast_cache_points": len(predictor.forecast_cache),
        "forecast_cache_age_s": cache_age_s,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
import asyncio
import csv
import json
import os
import random
import logging
import time
from aiocoap import resource, Context, Message

# -------------------- Configurazione --------------------
# Luce come percentuale 0-100, come il firmware reale (ADC mappato in %)
LIGHT_BASE = 40.0            # % di base
LIGHT_NOISE = 2.0            # variazione casuale

ACCEL_SAMPLE_RATE = 0.01     # 100 Hz (come vTaskDelay(10ms) nel firmware)
LIGHT_SAMPLE_RATE = 1.0      # come vTaskDelay(1000ms) nel firmware

# Soglie di default (allineate a main.c)
impact_threshold = 0.4        # g, differenza tra campioni successivi su Z
theft_threshold = 0.25        # g, scostamento dalla baseline su X (asse verticale reale)
THRESHOLD_MIN = 0.05
THRESHOLD_MAX = 5.00

THEFT_CONFIRM_SAMPLES = 6     # persistenza richiesta per confermare (come firmware)
THEFT_COOLDOWN_S = 3.0

# GPS: come GPS_PING_INTERVAL_MS in main.c (5000ms). Punto base + piccolo
# random walk per simulare lo spostamento dell'opera una volta rubata.
GPS_PING_INTERVAL_S = 5.0
GPS_BASE_LAT = 45.4642    # Milano, giusto per avere coordinate plausibili
GPS_BASE_LON = 9.1900
GPS_WALK_STEP = 0.0005    # ~50m per ping, cosi' il tracking si vede muoversi

# -------------------- Logging CSV (metriche/test) --------------------
# Metrica #4 (precision/recall theft):
#   - theft_ground_truth.csv: ogni volta che il mock INIETTA un furto simulato,
#     registra la finestra temporale attesa dell'iniezione [ts_start, ts_end_expected].
#   - detections.csv: ogni volta che l'algoritmo di detection CONFERMA un evento
#     (impact/theft), registra timestamp/asse/valore. Lo script di analisi
#     confronta le due liste per calcolare TP/FP/FN.
#
# Il campo "ts" aggiunto a tutti gli eventi (impact/theft/position) serve
# invece alla metrica #3 (latenza end-to-end evento -> notifica), calcolata
# lato mashup-app confrontandolo con l'orario di ricezione/notifica.
LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

GROUND_TRUTH_CSV = os.path.join(LOG_DIR, "theft_ground_truth.csv")
DETECTIONS_CSV = os.path.join(LOG_DIR, "detections.csv")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ESP-SEN-MOCK")


def _init_csv(path: str, header: list[str]):
    if not os.path.exists(path):
        try:
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(header)
        except Exception as exc:
            logger.warning("Impossibile inizializzare CSV '%s': %s", path, exc)


_init_csv(GROUND_TRUTH_CSV, ["injection_id", "ts_start", "ts_end_expected", "n_samples"])
_init_csv(DETECTIONS_CSV, ["ts", "type", "axis", "value"])

_injection_counter = 0


def log_ground_truth_injection(ts_start: float, ts_end_expected: float, n_samples: int):
    """Registra una finestra di iniezione furto simulata (ground truth)."""
    global _injection_counter
    _injection_counter += 1
    try:
        with open(GROUND_TRUTH_CSV, "a", newline="") as f:
            csv.writer(f).writerow([
                _injection_counter, f"{ts_start:.6f}", f"{ts_end_expected:.6f}", n_samples
            ])
    except Exception as exc:
        logger.warning("Errore scrittura theft_ground_truth.csv: %s", exc)


def log_detection(ts: float, ev_type: str, axis: str, value):
    """Registra una detection confermata (impact/theft) cosi' come uscita
    dall'algoritmo di rilevamento, indipendentemente dal fatto che
    corrisponda o meno a un'iniezione ground-truth (serve anche a scoprire
    i falsi positivi)."""
    try:
        with open(DETECTIONS_CSV, "a", newline="") as f:
            csv.writer(f).writerow([f"{ts:.6f}", ev_type, axis, value])
    except Exception as exc:
        logger.warning("Errore scrittura detections.csv: %s", exc)


# -------------------- Stato condiviso --------------------
light_percent = LIGHT_BASE

# Il sensore e' montato DI TAGLIO: la gravita' cade sull'asse X (non Z).
# A riposo: ax ~ 1.0g, ay ~ 0, az ~ 0 (allineato a main.c)
ax, ay, az = 1.0, 0.0, 0.0          # ultimo campione grezzo
last_ax, last_ay, last_az = 1.0, 0.0, 0.0
sum_ax = sum_ay = sum_az = 0.0
sample_count = 0
avg_ax = avg_ay = avg_az = 0.0      # esposto su /accel, come g_avg_* nel firmware

baseline_ax = 1.0                   # asse verticale reale (gravita' su X)
theft_counter = 0
last_theft_trigger = 0.0

# Stato tracking GPS: mirror di g_tracking_active nel firmware. Diventa
# True quando un furto viene confermato, si azzera solo con reset_alarm.
tracking_active = False

tracking_lock = asyncio.Lock()
gps_lat, gps_lon = GPS_BASE_LAT, GPS_BASE_LON

light_lock = asyncio.Lock()
accel_lock = asyncio.Lock()          # protegge ax/ay/az correnti E avg_*
threshold_lock = asyncio.Lock()
event_lock = asyncio.Lock()
event_queue = asyncio.Queue(maxsize=20)

# Riferimento globale alla risorsa CoAP osservabile /events, impostato in main()
# prima dell'avvio dei task. Serve a inoltrare le notifiche Observe (RFC 7641)
# ogni volta che push_event() aggiunge un nuovo evento, cosi' i client che
# fanno GET con observe=true ricevono il push in tempo reale.
_event_resource_ref = None


async def push_event(ev: dict):
    async with event_lock:
        try:
            event_queue.put_nowait(ev)
        except asyncio.QueueFull:
            _ = event_queue.get_nowait()
            event_queue.put_nowait(ev)

    # Metrica #4: logga ogni detection confermata (impact/theft) per il
    # confronto con il ground truth nello script di analisi. Gli eventi
    # "position" non sono rilevanti per precision/recall e vengono esclusi.
    if ev.get("type") in ("impact", "theft"):
        log_detection(ev.get("ts", time.time()), ev["type"], ev.get("axis", ""), ev.get("value", ""))

    # Notifica gli observer CoAP registrati su /events
    if _event_resource_ref is not None:
        _event_resource_ref.updated_state()


# -------------------- Simulatore luce --------------------
async def light_task():
    global light_percent
    while True:
        async with light_lock:
            light_percent += random.gauss(0, LIGHT_NOISE)
            light_percent = max(0.0, min(100.0, light_percent))
        await asyncio.sleep(LIGHT_SAMPLE_RATE)


# -------------------- Simulatore + rilevazione accelerometro --------------------
# Allineato a read_accelerometer_sensor() in main.c:
# - IMPACT  su Z (diff_z)  -> asse trasversale/longitudinale
# - THEFT   su X (ax - baseline_ax) -> asse verticale reale (gravita')
async def accelerometer_task():
    global ax, ay, az, last_ax, last_ay, last_az
    global sum_ax, sum_ay, sum_az, sample_count
    global baseline_ax, theft_counter, last_theft_trigger
    global tracking_active

    while True:
        # Vibrazioni normali attorno al riposo (gravita' su X)
        new_ax = 1.0 + random.gauss(0, 0.02)
        new_ay = random.gauss(0, 0.05)
        new_az = random.gauss(0, 0.05)

        # Ogni tanto inietta un impatto: picco isolato su Z (0.5% per campione)
        if random.random() < 0.005:
            new_az += random.uniform(0.6, 1.5) * random.choice([1, -1])
            logger.info("Simulazione impatto (picco su Z)")

        # Ogni tanto inietta un furto: scostamento sostenuto su X (asse verticale),
        # abbastanza lungo da superare THEFT_CONFIRM_SAMPLES campioni consecutivi
        theft_injection_samples = 0
        if random.random() < 0.0005:
            theft_injection_samples = THEFT_CONFIRM_SAMPLES + 4
            # Metrica #4: registra la finestra di iniezione (ground truth).
            # ts_end_expected e' una stima, dato che la durata reale
            # dell'iniezione dipende da quanti campioni vengono consumati
            # nel ciclo "for" qui sotto (ACCEL_SAMPLE_RATE per campione).
            ts_start = time.time()
            ts_end_expected = ts_start + theft_injection_samples * ACCEL_SAMPLE_RATE
            log_ground_truth_injection(ts_start, ts_end_expected, theft_injection_samples)
            logger.info("Simulazione furto (asse X spostato, iniezione sostenuta)")

        async with accel_lock:
            last_ax, last_ay, last_az = ax, ay, az
            ax, ay, az = new_ax, new_ay, new_az
            diff_z = az - last_az   # backward difference su Z per impact
            sum_ax += ax
            sum_ay += ay
            sum_az += az
            sample_count += 1

        # --- Rilevamento impatto su Z (fuori dal lock, come nel firmware) ---
        async with threshold_lock:
            impact_th = impact_threshold
            theft_th = theft_threshold

        if abs(diff_z) > impact_th:
            ev = {"type": "impact", "axis": "z", "value": round(diff_z, 3), "ts": time.time()}
            await push_event(ev)
            logger.warning(f"IMPACT DETECTED: diff_z={diff_z:.3f} > {impact_th}")

        # --- Rilevamento furto su X: baseline + contatore con isteresi ---
        if theft_injection_samples > 0:
            for _ in range(theft_injection_samples):
                injected_ax = 1.0 - random.uniform(0.5, 0.9)  # x molto basso (oggetto spostato dalla verticale)
                displacement = injected_ax - baseline_ax
                if abs(displacement) > theft_th:
                    theft_counter = min(theft_counter + 2, 50)
                else:
                    theft_counter = max(theft_counter - 1, 0)

                if theft_counter >= THEFT_CONFIRM_SAMPLES:
                    now = time.time()
                    if now - last_theft_trigger > THEFT_COOLDOWN_S:
                        ev = {"type": "theft", "axis": "x", "value": round(displacement, 3), "ts": now}
                        await push_event(ev)
                        logger.warning(f"THEFT DETECTED: displacement={displacement:.3f} > {theft_th}")
                        last_theft_trigger = now
                        async with tracking_lock:
                            tracking_active = True
                    theft_counter = 0
                await asyncio.sleep(ACCEL_SAMPLE_RATE)
        else:
            displacement = ax - baseline_ax
            if abs(displacement) > theft_th:
                theft_counter = min(theft_counter + 2, 50)
            else:
                theft_counter = max(theft_counter - 1, 0)

            if theft_counter >= THEFT_CONFIRM_SAMPLES:
                now = time.time()
                if now - last_theft_trigger > THEFT_COOLDOWN_S:
                    ev = {"type": "theft", "axis": "x", "value": round(displacement, 3), "ts": now}
                    await push_event(ev)
                    logger.warning(f"THEFT DETECTED: displacement={displacement:.3f} > {theft_th}")
                    last_theft_trigger = now
                    async with tracking_lock:
                        tracking_active = True
                theft_counter = 0

        await asyncio.sleep(ACCEL_SAMPLE_RATE)


# -------------------- Media 250ms (come accel_avg_task in main.c) --------------------
async def accel_avg_task():
    global sum_ax, sum_ay, sum_az, sample_count, avg_ax, avg_ay, avg_az
    while True:
        async with accel_lock:
            if sample_count > 0:
                avg_ax = sum_ax / sample_count
                avg_ay = sum_ay / sample_count
                avg_az = sum_az / sample_count
                sum_ax = sum_ay = sum_az = 0.0
                sample_count = 0
        # Ridotto a 250ms come nel firmware
        await asyncio.sleep(0.25)


# -------------------- GPS tracking (come gps_ping_task in main.c) --------------------
async def gps_task():
    global gps_lat, gps_lon
    while True:
        async with tracking_lock:
            tracking = tracking_active
        if tracking:
            async with tracking_lock:
                gps_lat += random.uniform(-GPS_WALK_STEP, GPS_WALK_STEP)
                gps_lon += random.uniform(-GPS_WALK_STEP, GPS_WALK_STEP)
            logger.info(f"Fix GPS: lat={gps_lat:.6f} lon={gps_lon:.6f}")
            ev = {"type": "position", "lat": round(gps_lat, 6), "lon": round(gps_lon, 6), "ts": time.time()}
            await push_event(ev)
            await asyncio.sleep(GPS_PING_INTERVAL_S)
        else:
            await asyncio.sleep(2.0)  # GPS_WARMUP_INTERVAL_MS


# -------------------- Server CoAP --------------------
class LightResource(resource.Resource):
    async def render_get(self, request):
        async with light_lock:
            payload = f"{light_percent:.1f}".encode()
        return Message(payload=payload)


class AccelResource(resource.Resource):
    """Espone la media, come /accel nel firmware reale."""
    async def render_get(self, request):
        async with accel_lock:
            data = {"ax": round(avg_ax, 3), "ay": round(avg_ay, 3), "az": round(avg_az, 3)}
        payload = json.dumps(data).encode()
        return Message(payload=payload, content_format=50)


class EventResource(resource.ObservableResource):
    """Restituisce UN evento per volta dalla coda (FIFO).
    Eredita da ObservableResource per supportare Observe (RFC 7641)."""
    async def render_get(self, request):
        event = None
        async with event_lock:
            if not event_queue.empty():
                try:
                    event = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    event = None
        payload = json.dumps(event).encode() if event is not None else b"{}"
        return Message(payload=payload, content_format=50)


class ThresholdsResource(resource.Resource):
    """GET /thresholds: entrambe le soglie insieme."""
    async def render_get(self, request):
        async with threshold_lock:
            data = {"impact": impact_threshold, "theft_displacement": theft_threshold}
        return Message(payload=json.dumps(data).encode(), content_format=50)


class ImpactThresholdResource(resource.Resource):
    """PUT /thresholds/impact: payload plain text, un solo float."""
    async def render_put(self, request):
        global impact_threshold
        try:
            value = float(request.payload.decode().strip())
        except Exception:
            return Message(code=128)  # 4.00 Bad Request
        if value < THRESHOLD_MIN or value > THRESHOLD_MAX:
            return Message(code=128)
        async with threshold_lock:
            impact_threshold = value
        logger.info(f"impact_threshold aggiornata: {value}")
        return Message(code=68)  # 2.04 Changed


class TheftThresholdResource(resource.Resource):
    """PUT /thresholds/theft: payload plain text, un solo float."""
    async def render_put(self, request):
        global theft_threshold
        try:
            value = float(request.payload.decode().strip())
        except Exception:
            return Message(code=128)
        if value < THRESHOLD_MIN or value > THRESHOLD_MAX:
            return Message(code=128)
        async with threshold_lock:
            theft_threshold = value
        logger.info(f"theft_threshold aggiornata: {value}")
        return Message(code=68)  # 2.04 Changed


class ResetAlarmResource(resource.Resource):
    """PUT /reset_alarm: ricalibra la baseline sull'ultima media X e ferma
    il tracking GPS, come hnd_put_reset_alarm nel firmware reale."""
    async def render_put(self, request):
        global baseline_ax, theft_counter, tracking_active
        async with accel_lock:
            current_ax = avg_ax
            theft_counter = 0
            baseline_ax = current_ax
        async with tracking_lock:
            tracking_active = False
        logger.info(f"Reset alarm: baseline_ax ricalibrata a {baseline_ax:.3f}, tracking disattivato")
        return Message(code=68)  # 2.04 Changed


async def main():
    global _event_resource_ref

    events_resource = EventResource()
    _event_resource_ref = events_resource

    # Avvio task di simulazione
    asyncio.create_task(light_task())
    asyncio.create_task(accelerometer_task())
    asyncio.create_task(accel_avg_task())
    asyncio.create_task(gps_task())

    # Server CoAP
    root = resource.Site()
    root.add_resource(['light'], LightResource())
    root.add_resource(['accel'], AccelResource())
    root.add_resource(['events'], events_resource)
    root.add_resource(['thresholds'], ThresholdsResource())
    root.add_resource(['thresholds', 'impact'], ImpactThresholdResource())
    root.add_resource(['thresholds', 'theft'], TheftThresholdResource())
    root.add_resource(['reset_alarm'], ResetAlarmResource())

    await Context.create_server_context(root, bind=('0.0.0.0', 5683))
    logger.info("CoAP server listening on port 5683")
    await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    asyncio.run(main())
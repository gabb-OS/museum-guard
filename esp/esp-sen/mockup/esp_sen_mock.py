import asyncio
import json
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
impact_threshold = 0.4        # g, differenza tra campioni successivi su X
theft_threshold = 0.35        # g, scostamento dalla baseline su Z
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

# -------------------- Stato condiviso --------------------
light_percent = LIGHT_BASE

ax, ay, az = 0.0, 0.0, 1.0          # ultimo campione grezzo
last_ax, last_ay, last_az = 0.0, 0.0, 1.0

sum_ax = sum_ay = sum_az = 0.0
sample_count = 0
avg_ax = avg_ay = avg_az = 0.0      # esposto su /accel, come g_avg_* nel firmware

baseline_az = 1.0
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
# fanno GET con observe=true (vedi coap-client.js) ricevono il push in tempo
# reale invece di dover fare polling.
_event_resource_ref = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ESP-SEN-MOCK")


async def push_event(ev: dict):
    async with event_lock:
        try:
            event_queue.put_nowait(ev)
        except asyncio.QueueFull:
            _ = event_queue.get_nowait()
            event_queue.put_nowait(ev)
    # Notifica gli observer CoAP registrati su /events (fuori dal lock,
    # updated_state() e' sincrona e itera sugli observer correnti).
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
# Stessa struttura del task read_accelerometer_sensor() in main.c:
# lettura -> diff_x per impatto -> displacement da baseline per furto (con isteresi)
async def accelerometer_task():
    global ax, ay, az, last_ax, last_ay, last_az
    global sum_ax, sum_ay, sum_az, sample_count
    global baseline_az, theft_counter, last_theft_trigger
    global tracking_active

    while True:
        # Vibrazioni normali attorno al riposo (gravità su Z)
        new_ax = random.gauss(0, 0.05)
        new_ay = random.gauss(0, 0.05)
        new_az = 1.0 + random.gauss(0, 0.02)

        # Ogni tanto inietta un impatto: picco isolato su X (0.5% per campione)
        if random.random() < 0.005:
            new_ax += random.uniform(0.6, 1.5) * random.choice([1, -1])
            logger.info("Simulazione impatto (picco su X)")

        # Ogni tanto inietta un furto: scostamento sostenuto su Z, abbastanza
        # lungo da superare THEFT_CONFIRM_SAMPLES campioni consecutivi (0.05% per campione)
        theft_injection_samples = 0
        if random.random() < 0.0005:
            theft_injection_samples = THEFT_CONFIRM_SAMPLES + 4
            logger.info("Simulazione furto (asse Z spostato, iniezione sostenuta)")

        async with accel_lock:
            last_ax, last_ay, last_az = ax, ay, az
            ax, ay, az = new_ax, new_ay, new_az

            diff_x = ax - last_ax  # backward difference, come nel firmware

            sum_ax += ax
            sum_ay += ay
            sum_az += az
            sample_count += 1

        # --- Rilevamento impatto (fuori dal lock, come nel firmware) ---
        async with threshold_lock:
            impact_th = impact_threshold
            theft_th = theft_threshold

        if abs(diff_x) > impact_th:
            ev = {"type": "impact", "axis": "x", "value": round(diff_x, 3)}
            await push_event(ev)
            logger.warning(f"IMPACT DETECTED: diff_x={diff_x:.3f} > {impact_th}")

        # --- Rilevamento furto: baseline + contatore con isteresi ---
        if theft_injection_samples > 0:
            for _ in range(theft_injection_samples):
                injected_az = 1.0 - random.uniform(0.5, 0.9)  # z molto basso
                displacement = injected_az - baseline_az
                if abs(displacement) > theft_th:
                    theft_counter = min(theft_counter + 2, 50)
                else:
                    theft_counter = max(theft_counter - 1, 0)

                if theft_counter >= THEFT_CONFIRM_SAMPLES:
                    now = time.time()
                    if now - last_theft_trigger > THEFT_COOLDOWN_S:
                        ev = {"type": "theft", "axis": "z", "value": round(displacement, 3)}
                        await push_event(ev)
                        logger.warning(f"THEFT DETECTED: displacement={displacement:.3f} > {theft_th}")
                        last_theft_trigger = now
                        async with tracking_lock:
                            tracking_active = True
                    theft_counter = 0
                await asyncio.sleep(ACCEL_SAMPLE_RATE)
        else:
            displacement = az - baseline_az
            if abs(displacement) > theft_th:
                theft_counter = min(theft_counter + 2, 50)
            else:
                theft_counter = max(theft_counter - 1, 0)

            if theft_counter >= THEFT_CONFIRM_SAMPLES:
                now = time.time()
                if now - last_theft_trigger > THEFT_COOLDOWN_S:
                    ev = {"type": "theft", "axis": "z", "value": round(displacement, 3)}
                    await push_event(ev)
                    logger.warning(f"THEFT DETECTED: displacement={displacement:.3f} > {theft_th}")
                    last_theft_trigger = now
                    async with tracking_lock:
                        tracking_active = True
                theft_counter = 0

        await asyncio.sleep(ACCEL_SAMPLE_RATE)


# -------------------- Media 1s (come accel_avg_task in main.c) --------------------
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
        await asyncio.sleep(1.0)


# -------------------- GPS tracking (come gps_ping_task in main.c) --------------------
# Finche' tracking_active e' True (furto confermato, non ancora resettato)
# pinga una posizione GPS ogni GPS_PING_INTERVAL_S e la pusha come evento
# "position" sullo stesso canale /events di impact/theft. Un piccolo random
# walk simula lo spostamento dell'opera rubata invece di un punto fisso.
async def gps_task():
    global gps_lat, gps_lon
    while True:
        async with tracking_lock:
            tracking = tracking_active
        if tracking:
            gps_lat += random.uniform(-GPS_WALK_STEP, GPS_WALK_STEP)
            gps_lon += random.uniform(-GPS_WALK_STEP, GPS_WALK_STEP)
            logger.info(f"Fix GPS: lat={gps_lat:.6f} lon={gps_lon:.6f}")
            ev = {"type": "position", "lat": round(gps_lat, 6), "lon": round(gps_lon, 6)}
            await push_event(ev)
            await asyncio.sleep(GPS_PING_INTERVAL_S)
        else:
            await asyncio.sleep(0.5)


# -------------------- Server CoAP --------------------
class LightResource(resource.Resource):
    async def render_get(self, request):
        async with light_lock:
            payload = f"{light_percent:.1f}".encode()
        return Message(payload=payload)


class AccelResource(resource.Resource):
    """Espone la media su 1s, come /accel nel firmware reale."""
    async def render_get(self, request):
        async with accel_lock:
            data = {"ax": round(avg_ax, 3), "ay": round(avg_ay, 3), "az": round(avg_az, 3)}
        payload = json.dumps(data).encode()
        return Message(payload=payload, content_format=50)


class EventResource(resource.ObservableResource):
    """Restituisce UN evento per volta dalla coda (FIFO), come il firmware
    reale che tiene solo l'ultimo evento pronto per il prossimo GET
    (coap_get_last_event in coap_server.c).

    Eredita da ObservableResource (non dal semplice Resource) cosi' da
    supportare l'opzione CoAP Observe: quando un client fa GET con
    observe=true (come fa coap-client.js in espSen-adapter.js), aiocoap
    lo registra come observer. push_event() chiama poi self.updated_state()
    ogni volta che arriva un nuovo evento, il che fa ri-eseguire render_get()
    e invia il risultato in push a tutti gli observer registrati, senza
    bisogno di polling lato client.

    Se in coda ci sono piu' eventi accumulati (es. raffica ravvicinata),
    updated_state() viene comunque richiamata una volta per ogni push_event(),
    quindi ogni notifica corrisponde a un GET e quindi a un evento estratto:
    la coda si svuota nel tempo, un item alla volta, esattamente come
    accadrebbe con hardware reale a raffica di eventi ravvicinati.
    """
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
    """GET /thresholds: entrambe le soglie insieme, come nel firmware."""
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
    """PUT /reset_alarm: ricalibra la baseline sull'ultima media Z e ferma
    il tracking GPS, come hnd_put_reset_alarm nel firmware reale."""
    async def render_put(self, request):
        global baseline_az, theft_counter, tracking_active
        async with accel_lock:
            current_az = avg_az
        theft_counter = 0
        baseline_az = current_az
        async with tracking_lock:
            tracking_active = False
        logger.info(f"Reset alarm: baseline_az ricalibrata a {baseline_az:.3f}, tracking disattivato")
        return Message(code=68)  # 2.04 Changed


async def main():
    global _event_resource_ref

    # Risorsa /events creata PRIMA di avviare i task di simulazione, cosi'
    # _event_resource_ref e' gia' valido quando push_event() viene chiamata
    # per la prima volta (evita di perdere il primo evento generato).
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
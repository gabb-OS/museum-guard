import asyncio
import json
import random
import logging
from aiocoap import resource, Context, Message, POST, PUT

# -------------------- Configurazione --------------------
LIGHT_BASE = 300.0          # lux di base
LIGHT_NOISE = 5.0           # variazione casuale
ACCEL_SAMPLE_RATE = 0.02    # 50 Hz
LIGHT_SAMPLE_RATE = 0.5     # 2 Hz

# Soglie di default (modificabili via CoAP)
impact_threshold = 2.5      # g (accelerazione brusca longitudinale)
theft_threshold   = 0.5     # deviazione verticale prolungata (differenza da 1g)

# Variabili globali con mutex (asyncio.Lock)
light_value = LIGHT_BASE
accel_x = 0.0
accel_y = 0.0
accel_z = 1.0
last_impact = None
last_theft = None

light_lock = asyncio.Lock()
accel_lock = asyncio.Lock()
event_lock = asyncio.Lock()

# Code (simili a FreeRTOS queues)
raw_accel_queue = asyncio.Queue(maxsize=50)   # coda di campioni grezzi
event_queue = asyncio.Queue(maxsize=20)       # coda di eventi rilevati

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ESP-SEN-MOCK")

# -------------------- Simulatore luce --------------------
async def light_task():
    global light_value
    while True:
        async with light_lock:
            light_value += random.gauss(0, LIGHT_NOISE)
            light_value = max(0, min(1000, light_value))
        await asyncio.sleep(LIGHT_SAMPLE_RATE)

# -------------------- Simulatore accelerometro --------------------
async def accelerometer_task():
    global accel_x, accel_y, accel_z
    while True:
        # Simula accelerazione: gravità lungo Z, piccole vibrazioni su X/Y
        ax = random.gauss(0, 0.05)
        ay = random.gauss(0, 0.05)
        az = 1.0 + random.gauss(0, 0.02)

        # Ogni tanto inietta un impatto (breve picco sull'asse X longitudinale)
        if random.random() < 0.005:   # 0.5% di probabilità a ogni campione
            ax += random.uniform(5.0, 10.0) * random.choice([1, -1])
            logger.info("Simulazione impatto (picco su X)")

        # Ogni tanto simula furto (asse Z scende sotto 1g per diversi secondi)
        if random.random() < 0.001:   # raro
            logger.info("Simulazione furto (asse Z ridotto)")
            # Per il furto, verrà gestito nella rilevazione come deviazione prolungata
            # Inietto un offset persistente per qualche campione
            await raw_accel_queue.put((ax, ay, 0.2))   # Z molto basso
            await asyncio.sleep(0.5)                   # persiste
            await raw_accel_queue.put((ax, ay, 0.3))
            await asyncio.sleep(0.5)
            await raw_accel_queue.put((ax, ay, 0.4))
            continue   # salta il campione normale

        # Invia alla coda grezza
        try:
            raw_accel_queue.put_nowait((ax, ay, az))
        except asyncio.QueueFull:
            # Scarta il campione più vecchio se la coda è piena (simula overwrite)
            _ = raw_accel_queue.get_nowait()
            raw_accel_queue.put_nowait((ax, ay, az))

        async with accel_lock:
            accel_x, accel_y, accel_z = ax, ay, az

        await asyncio.sleep(ACCEL_SAMPLE_RATE)

# -------------------- Rilevazione eventi --------------------
async def detection_task():
    global last_impact, last_theft
    # Buffer circolare per la deviazione verticale (furto)
    z_history = []
    theft_duration = 2.0   # secondi di deviazione persistenti per dichiarare furto
    samples_needed = int(theft_duration / ACCEL_SAMPLE_RATE)

    while True:
        try:
            ax, ay, az = await asyncio.wait_for(raw_accel_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            continue

        # Rilevamento impatto: |ax| > soglia (ipotizzando asse longitudinale X)
        if abs(ax) > impact_threshold:
            async with event_lock:
                last_impact = {
                    "timestamp": asyncio.get_event_loop().time(),
                    "value": ax,
                    "type": "impact"
                }
            await event_queue.put(last_impact)
            logger.warning(f"IMPACT DETECTED: ax={ax:.2f} > {impact_threshold}")

        # Rilevamento furto: deviazione verticale media rispetto a 1g per un periodo
        z_history.append(az)
        if len(z_history) > samples_needed:
            z_history.pop(0)
            avg_z = sum(z_history) / len(z_history)
            if abs(avg_z - 1.0) > theft_threshold:
                async with event_lock:
                    last_theft = {
                        "timestamp": asyncio.get_event_loop().time(),
                        "value": avg_z,
                        "type": "theft"
                    }
                await event_queue.put(last_theft)
                logger.warning(f"THEFT DETECTED: avg_z={avg_z:.2f} deviation={abs(avg_z-1.0):.2f} > {theft_threshold}")
                # Svuota la history per evitare segnalazioni multiple
                z_history.clear()

# -------------------- Server CoAP --------------------
class LightResource(resource.Resource):
    async def render_get(self, request):
        async with light_lock:
            payload = f"{light_value:.2f}".encode()
        return Message(payload=payload)

class AccelResource(resource.Resource):
    async def render_get(self, request):
        async with accel_lock:
            data = {"x": round(accel_x, 3), "y": round(accel_y, 3), "z": round(accel_z, 3)}
        payload = json.dumps(data).encode()
        return Message(payload=payload, content_format=50)

class EventResource(resource.Resource):
    """Restituisce tutti gli eventi in coda (FIFO) e li rimuove"""
    async def render_get(self, request):
        events = []
        while not event_queue.empty():
            try:
                ev = event_queue.get_nowait()
                events.append(ev)
            except asyncio.QueueEmpty:
                break
        payload = json.dumps(events).encode() if events else b"[]"
        return Message(payload=payload, content_format=50)

class ThresholdResource(resource.Resource):
    """GET/PUT per le soglie"""
    async def render_get(self, request):
        data = {
            "impact_threshold": impact_threshold,
            "theft_threshold": theft_threshold
        }
        return Message(payload=json.dumps(data).encode(), content_format=50)

    async def render_put(self, request):
        global impact_threshold, theft_threshold
        try:
            new = json.loads(request.payload.decode())
            if "impact_threshold" in new:
                impact_threshold = float(new["impact_threshold"])
            if "theft_threshold" in new:
                theft_threshold = float(new["theft_threshold"])
            logger.info(f"Thresholds updated: impact={impact_threshold}, theft={theft_threshold}")
            return Message(payload=b"OK", code=65)   # 2.04 Changed
        except Exception as e:
            return Message(payload=str(e).encode(), code=128)   # 4.00 Bad Request

async def main():
    # Avvio task di simulazione
    asyncio.create_task(light_task())
    asyncio.create_task(accelerometer_task())
    asyncio.create_task(detection_task())

    # Server CoAP
    root = resource.Site()
    root.add_resource(['light'], LightResource())
    root.add_resource(['accel'], AccelResource())
    root.add_resource(['events'], EventResource())
    root.add_resource(['thresholds'], ThresholdResource())

    await Context.create_server_context(root, bind=('0.0.0.0', 5683))
    logger.info("CoAP server listening on port 5683")
    await asyncio.get_running_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())
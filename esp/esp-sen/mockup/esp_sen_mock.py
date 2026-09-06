import asyncio
import json
import random
import logging
import time
from aiocoap import resource, Context, Message

# -------------------- Configuration --------------------
# Light as 0-100%, matching the real firmware (ADC mapped to %)
LIGHT_BASE = 40.0            # Base percentage
LIGHT_NOISE = 2.0            # Random variation

ACCEL_SAMPLE_RATE = 0.01     # 100 Hz (matches vTaskDelay(10ms) in firmware)
LIGHT_SAMPLE_RATE = 1.0      # Matches vTaskDelay(1000ms) in firmware

# Default thresholds 
impact_threshold = 0.4        # g, difference between consecutive Z samples
theft_threshold = 0.25        # g, deviation from baseline on X (actual vertical axis)
THRESHOLD_MIN = 0.05
THRESHOLD_MAX = 5.00

THEFT_CONFIRM_SAMPLES = 6     # Persistence required for confirmation (matches firmware)
THEFT_COOLDOWN_S = 3.0

# GPS: matches GPS_PING_INTERVAL_MS in main.c (5000ms). Base point + small
# random walk to simulate artwork displacement once stolen.
GPS_PING_INTERVAL_S = 5.0
GPS_BASE_LAT = 45.4642        # Milan, provides plausible coordinates
GPS_BASE_LON = 9.1900
GPS_WALK_STEP = 0.0005        # ~50m per ping, so tracking movement is visible

# -------------------- Shared State --------------------
light_percent = LIGHT_BASE

# Sensor is mounted sideways: gravity falls on the X axis
# At rest: ax ~ 1.0g, ay ~ 0, az ~ 0 
ax, ay, az = 1.0, 0.0, 0.0          # Latest raw sample
last_ax, last_ay, last_az = 1.0, 0.0, 0.0
sum_ax = sum_ay = sum_az = 0.0
sample_count = 0
avg_ax = avg_ay = avg_az = 0.0      # Exposed on /accel, like g_avg_* in firmware

baseline_ax = 1.0                   # Actual vertical axis
theft_counter = 0
last_theft_trigger = 0.0

# GPS tracking state: mirrors g_tracking_active in firmware. Becomes
# True when theft is confirmed, resets only via reset_alarm.
tracking_active = False

tracking_lock = asyncio.Lock()
gps_lat, gps_lon = GPS_BASE_LAT, GPS_BASE_LON

light_lock = asyncio.Lock()
accel_lock = asyncio.Lock()          # Protects current ax/ay/az AND avg_*
threshold_lock = asyncio.Lock()
event_lock = asyncio.Lock()
event_queue = asyncio.Queue(maxsize=20)

# Global reference to the observable CoAP /events resource, set in main()
# before tasks start. Used to forward Observe notifications (RFC 7641)
# whenever push_event() adds a new event, so clients that
# do GET with observe=true receive real-time pushes.
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

    # Notify CoAP observers registered on /events
    if _event_resource_ref is not None:
        _event_resource_ref.updated_state()


# -------------------- Light Simulator --------------------
async def light_task():
    global light_percent
    while True:
        async with light_lock:
            light_percent += random.gauss(0, LIGHT_NOISE)
            light_percent = max(0.0, min(100.0, light_percent))
        await asyncio.sleep(LIGHT_SAMPLE_RATE)


# -------------------- Accelerometer Simulator + Detection --------------------
# Aligned with read_accelerometer_sensor() in main.c:
# - IMPACT on Z (diff_z) -> transverse/longitudinal axis
# - THEFT on X (ax - baseline_ax) -> actual vertical axis (gravity)
async def accelerometer_task():
    global ax, ay, az, last_ax, last_ay, last_az
    global sum_ax, sum_ay, sum_az, sample_count
    global baseline_ax, theft_counter, last_theft_trigger
    global tracking_active

    while True:
        # Normal vibrations around rest (gravity on X)
        new_ax = 1.0 + random.gauss(0, 0.02)
        new_ay = random.gauss(0, 0.05)
        new_az = random.gauss(0, 0.05)

        # Occasionally inject an impact: isolated peak on Z (0.5% per sample)
        if random.random() < 0.005:
            new_az += random.uniform(0.6, 1.5) * random.choice([1, -1])
            logger.info("Simulating impact (peak on Z)")

        # Occasionally inject a theft: sustained deviation on X (vertical axis),
        # long enough to exceed THEFT_CONFIRM_SAMPLES consecutive samples
        theft_injection_samples = 0
        if random.random() < 0.0005:
            theft_injection_samples = THEFT_CONFIRM_SAMPLES + 4
            logger.info("Simulating theft (X axis shifted, sustained injection)")

        async with accel_lock:
            last_ax, last_ay, last_az = ax, ay, az
            ax, ay, az = new_ax, new_ay, new_az
            diff_z = az - last_az   # Backward difference on Z for impact
            sum_ax += ax
            sum_ay += ay
            sum_az += az
            sample_count += 1

        # --- Z impact detection (outside lock, like in firmware) ---
        async with threshold_lock:
            impact_th = impact_threshold
            theft_th = theft_threshold

        if abs(diff_z) > impact_th:
            ev = {"type": "impact", "axis": "z", "value": round(diff_z, 3)}
            await push_event(ev)
            logger.warning(f"IMPACT DETECTED: diff_z={diff_z:.3f} > {impact_th}")

        # --- X theft detection: baseline + counter with hysteresis ---
        if theft_injection_samples > 0:
            for _ in range(theft_injection_samples):
                injected_ax = 1.0 - random.uniform(0.5, 0.9)  # x very low (object moved from vertical)
                displacement = injected_ax - baseline_ax
                if abs(displacement) > theft_th:
                    theft_counter = min(theft_counter + 2, 50)
                else:
                    theft_counter = max(theft_counter - 1, 0)

                if theft_counter >= THEFT_CONFIRM_SAMPLES:
                    now = time.time()
                    if now - last_theft_trigger > THEFT_COOLDOWN_S:
                        ev = {"type": "theft", "axis": "x", "value": round(displacement, 3)}
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
                    ev = {"type": "theft", "axis": "x", "value": round(displacement, 3)}
                    await push_event(ev)
                    logger.warning(f"THEFT DETECTED: displacement={displacement:.3f} > {theft_th}")
                    last_theft_trigger = now
                    async with tracking_lock:
                        tracking_active = True
                theft_counter = 0

        await asyncio.sleep(ACCEL_SAMPLE_RATE)


# -------------------- 250ms Average (like accel_avg_task in main.c) --------------------
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
        # Reduced to 250ms as in firmware
        await asyncio.sleep(0.25)


# -------------------- GPS Tracking (like gps_ping_task in main.c) --------------------
async def gps_task():
    global gps_lat, gps_lon
    while True:
        async with tracking_lock:
            tracking = tracking_active
        if tracking:
            async with tracking_lock:
                gps_lat += random.uniform(-GPS_WALK_STEP, GPS_WALK_STEP)
                gps_lon += random.uniform(-GPS_WALK_STEP, GPS_WALK_STEP)
            logger.info(f"GPS Fix: lat={gps_lat:.6f} lon={gps_lon:.6f}")
            ev = {"type": "position", "lat": round(gps_lat, 6), "lon": round(gps_lon, 6)}
            await push_event(ev)
            await asyncio.sleep(GPS_PING_INTERVAL_S)
        else:
            await asyncio.sleep(2.0)  # GPS_WARMUP_INTERVAL_MS


# -------------------- CoAP Server --------------------
class LightResource(resource.Resource):
    async def render_get(self, request):
        async with light_lock:
            payload = f"{light_percent:.1f}".encode()
        return Message(payload=payload)


class AccelResource(resource.Resource):
    """Expose the average, like /accel in the real firmware."""
    async def render_get(self, request):
        async with accel_lock:
            data = {"ax": round(avg_ax, 3), "ay": round(avg_ay, 3), "az": round(avg_az, 3)}
        payload = json.dumps(data).encode()
        return Message(payload=payload, content_format=50)


class EventResource(resource.ObservableResource):
    """Return ONE event at a time from the queue (FIFO).
    Inherits from ObservableResource to support Observe (RFC 7641)."""
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
    """GET /thresholds: both thresholds together."""
    async def render_get(self, request):
        async with threshold_lock:
            data = {"impact": impact_threshold, "theft_displacement": theft_threshold}
        return Message(payload=json.dumps(data).encode(), content_format=50)


class ImpactThresholdResource(resource.Resource):
    """PUT /thresholds/impact: plain text payload, a single float."""
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
        logger.info(f"impact_threshold updated: {value}")
        return Message(code=68)  # 2.04 Changed


class TheftThresholdResource(resource.Resource):
    """PUT /thresholds/theft: plain text payload, a single float."""
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
        logger.info(f"theft_threshold updated: {value}")
        return Message(code=68)  # 2.04 Changed


class ResetAlarmResource(resource.Resource):
    """PUT /reset_alarm: recalibrate baseline to the latest X average and stop
    GPS tracking, like hnd_put_reset_alarm in the real firmware."""
    async def render_put(self, request):
        global baseline_ax, theft_counter, tracking_active
        async with accel_lock:
            current_ax = avg_ax
            theft_counter = 0
            baseline_ax = current_ax
        async with tracking_lock:
            tracking_active = False
        logger.info(f"Reset alarm: baseline_ax recalibrated to {baseline_ax:.3f}, tracking disabled")
        return Message(code=68)  # 2.04 Changed


async def main():
    global _event_resource_ref

    events_resource = EventResource()
    _event_resource_ref = events_resource

    # Start simulation tasks
    asyncio.create_task(light_task())
    asyncio.create_task(accelerometer_task())
    asyncio.create_task(accel_avg_task())
    asyncio.create_task(gps_task())

    # CoAP Server
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
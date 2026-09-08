# MuseumGuard

**W3C WoT-based Smart Artwork Protection System**
*Project for the Internet of Things course (A.Y. 2025-2026) — University of Bologna (Unibo)*

---

## Project Description

**MuseumGuard** is a distributed IoT system designed for continuous monitoring and active protection of artworks in museum environments. The system combines environmental monitoring, timely threat detection (accidental impacts and theft attempts), adaptive lighting control, and interoperability based on the **W3C Web of Things (WoT)** standard.

### General Architecture

```text
  +------------------+        CoAP       +--------------------+
  | ESP-SEN (Sensing)| <---------------->|                    |
  +------------------+                   |   WoT Controller   |
  +------------------+        HTTP       |                    |
  | ESP-ACT (Actuat.)| <---------------->+---------+----------+
  +------------------+                             |
                                                   v
                                         +----------------------+
                                         |  Mash-up Application |
                                         +----+---------+---+---+
                                              |       |     |
                             +----------------+       |     +----------+
                             |                        |                |
                             v                        v                v
                       +----------+           +--------------------+ +------------+
                       | InfluxDB | <-------> | predictive-light   | | Telegram   |
                       +----+-----+           | (Python/FastAPI)   | | Alert Bot  |
                            |                 +--------------------+ +------------+
                            v
                       +----------+
                       | Grafana  |
                       +----------+
```

The sensing and actuation nodes never communicate directly with each other: flow orchestration and application logic are handled by the **WoT Controller** and the **Mash-up** application. The `predictive-light` service reads history from InfluxDB and is only queried by the Mash-up to obtain the brightness estimate — it never talks directly to the devices.

---

## System Components

### 1. ESP-SEN — Sensing Node

* **Firmware:** ESP-IDF with FreeRTOS tasks dedicated to acquisition and transmission (`esp/esp-sen/sensing`).
* **Hardware:** ESP32, ambient light sensor (photoresistor on ADC), 3-axis I2C accelerometer (MPU6050), UART GPS module.
* **Features:**
  * Periodic reading of ambient light intensity and triaxial acceleration.
  * **Accidental impact** detection (sudden variation on the Z axis relative to the previous sample).
  * **Theft** detection (sustained displacement on the X axis relative to a baseline, confirmed over several consecutive samples with anti-bounce cooldown).
  * GPS tracking automatically activated after a confirmed theft, until a reset is received.
  * Dynamic runtime configuration of impact/theft thresholds, without recompilation.
* **Protocol:** CoAP (Constrained Application Protocol), UDP port `5683`.
* **Mock:** Python (`esp/esp-sen/mockup/esp_sen_mock.py`), same CoAP interface as the real firmware.

### 2. ESP-ACT — Actuation Node

* **Firmware:** ESP-IDF with FreeRTOS tasks for LED management and HTTP endpoints (`esp/esp-act/actuating`).
* **Hardware:** ESP32, PWM LED (artwork illumination, dimmable via `ledc`), dedicated digital LED for impact/theft.
* **Features:**
  * Adaptive control of artwork illumination via PWM dimming (0–100%).
  * **Impact signaling:** alarm LED blinks for 20 seconds (software timer), interruptible by a theft event.
  * **Theft signaling:** alarm LED turns on permanently until manually reset.
* **Protocol:** HTTP, port `80` on the real device (exposed as `8081` on the mock via Docker).
* **Mock:** Node.js (`esp/esp-act/mockup/server.js`, `mockup-act.js`), same HTTP interface as the real firmware.

### 3. WoT Controller (PC)

Node.js application (`wot-controller/`) that abstracts the two physical devices, exposing them as standardized **W3C WoT Things** via `@node-wot`, reachable at `http://localhost:8080`:

* **`sensor` Thing** (`/sensor`) — exposes ESP-SEN:
  * *Properties:* `ambientLight`, `accelerometer` (`{ax, ay, az}`), `thresholds` (`{impact, theft_displacement}`) — all observable.
  * *Actions:* `setImpactThreshold`, `setTheftThreshold`, `resetTracking`.
  * *Events:* `alarmEvent` — notifies `impact` / `theft` / `position`.
* **`actuator` Thing** (`/actuator`) — exposes ESP-ACT:
  * *Properties:* `artworkLedBrightness`, `alarmLightState` (`IDLE`/`IMPACT`/`THEFT`) — observable.
  * *Actions:* `regulateBrightness`, `triggerImpactBlink`, `triggerTheftAlarm`, `resetAlarmLight`.

The adapters towards the physical devices (CoAP for the sensor, HTTP for the actuator) live in `wot-controller/src/adapters/`.

### 4. Mash-up Application (PC)

Represents the logical core of the system (`mashup-app/`):

* Consumes the Things exposed by the WoT Controller (`clients/wotConsumer.js`).
* Performs periodic req/res polling of sensor + actuator and persists telemetry, events, and thresholds to **InfluxDB** (`logic/telemetryPoller.js`, `services/influxService.js`).
* Subscribes to the `alarmEvent` event in pub/sub mode and reacts to impacts/thefts by triggering the corresponding actuators and sending Telegram notifications (`logic/alarmHandler.js`).
* Queries the `predictive-light` service for the target brightness, with an explicit reactive fallback in case of error/timeout (`services/predictiveLightService.js`).
* Exposes a small internal REST API (`api/server.js`, port `3001`) used by Grafana's form panels for actions such as alarm reset and threshold updates, without Grafana needing to talk directly to the WoT Controller.

### 5. Predictive Lighting Service

Standalone Python/FastAPI microservice (`predictive-light/App.py`), containerized separately, based on a **"refit-once, serve-many"** architecture: the expensive ARIMA re-fit and the serving of predictions to the Mash-up are decoupled, so the service can respond to `/predict` with fine granularity without having to refit the model on every call.

* Every `PREDICT_INTERVAL_S` seconds (default `60`) it re-fits an **ARIMA** model (`pmdarima`) on the rolling window of `ambient_light` read from InfluxDB (`HISTORY_WINDOW_S` seconds), and in a single pass generates an **array of future forecasts** (`forecast_cache`), one every `FORECAST_STEP_S` seconds (default `1`), covering the interval up to the next refit.
* `GET /predict` **never re-fits** the model: it only computes how much time has elapsed since the last refit and returns the cache point closest to "now" — near-zero computational cost, regardless of how often the Mash-up polls the endpoint.
* Applies an **adaptive bias correction**: it maintains an EMA (`EMA_ALPHA`) over past forecast errors. Reconciliation still operates on a single reference point per refit cycle, selected at `PREDICTION_HORIZON_S` seconds after the fit (dedicated `prediction_error` measurement); that point is now extracted from the cache instead of being the sole output of the fit, but the reconciliation logic itself is unchanged.
* Exposes `GET /predict` → `{"predicted_ambient_light": <float>}` (Photoresistor target, predicted ambient light) and `GET /health` for diagnostics (also includes `forecast_cache_points` and `forecast_cache_age_s`); returns `503` until a valid fit is available, so the Mash-up falls back to the reactive rule.
* Writes both the forecasts (`predicted_light`) and the reconciliation errors (`prediction_error`) to InfluxDB, both viewable in Grafana and tagged with `system=museumguard`, consistent with the rest of the measurements written by the Mash-up.


### 6. Data Storage & Visualization

* **InfluxDB 2.7:** Time-series database for telemetry (`ambient_light`, `acceleration`, `actuator_state`), events (`impact_event`, `theft_event`, `position`), thresholds (`device_thresholds`), and predictive data (`predicted_light`, `prediction_error`). Bucket retention set via `INFLUXDB_RETENTION` (default `30d`) by `influxdb/init/01-setup.sh` on first startup.
* **Grafana:** `MuseumGuard` dashboard automatically provisioned (`grafana/dashboards/museumguard.json`) with environmental charts, impact/theft event tables, GPS map, actuator state, predicted-vs-actual light comparison, prediction error, and form panels (`volkovlabs-form-panel` plugin) for alarm reset and threshold configuration — the latter call the Mash-up API (`http://localhost:3001/api/...`) directly.

### 7. Telegram Alert Bot

`services/telegramService.js` module in the Mash-up: sends fire-and-forget text notifications to the configured chat whenever `alarmHandler.js` receives an `impact` or `theft` event. If `TELEGRAM_BOT_TOKEN`/`TELEGRAM_BOT_CHAT_ID` are not set, the bot remains disabled without blocking the rest of the system.

---

## Implemented Bonus Features

* **Configurable Detection Thresholds:** runtime updates of the impact (`thresholds/impact`) and theft (`thresholds/theft`) thresholds through the WoT Controller, without firmware recompilation, with range validated on the firmware side (`THRESHOLD_MIN`–`THRESHOLD_MAX`, 0.05–5.00 g).
* **Predictive Lighting Control:** ARIMA model with adaptive bias correction and a "refit-once, serve-many" architecture (periodic refit every `PREDICT_INTERVAL_S`, predictions served with `FORECAST_STEP_S` granularity from an in-memory cache) that estimates ambient light over the next `PREDICTION_HORIZON_S` seconds and proactively regulates the PWM LED, with automatic reactive fallback if the predictive service is unavailable.
* **Telegram Alert Bot:** instant notifications in case of emergency (impact/theft).
* **GPS:** To make the prototype more realistic, it was decided to add a GPS to provide the artwork's location in the event of theft. 

---

## Repository Structure

```text
museum-guard/
├── esp/
│   ├── esp-act/
│   │   ├── actuating/         # ESP-IDF firmware (Actuation Node)
│   │   │   └── main/          # main.c, networkConnect.c, shared.h
│   │   └── mockup/            # Node.js mock (server.js, mockup-act.js)
│   └── esp-sen/
│       ├── sensing/           # ESP-IDF firmware (Sensing Node)
│       │   └── main/
│       │       └── mylib/     # accelerometer, gps, wifi, coap_server
│       └── mockup/            # Python mock (esp_sen_mock.py)
├── wot-controller/            # W3C WoT Controller (Node.js / node-wot)
│   ├── src/
│   │   ├── adapters/          # espAct-adapter.js, espSen-adapter.js
│   │   ├── coap/              # coap-client.js
│   │   ├── things/            # espActThing.js, espSenThing.js
│   │   └── index.js
│   └── mockup/                # test scripts (testActThing.js)
├── mashup-app/                # Mash-up Application (Node.js)
│   └── src/
│       ├── api/                # server.js — internal REST API (port 3001)
│       ├── clients/             # wotConsumer.js
│       ├── logic/               # telemetryPoller.js, alarmHandler.js
│       ├── services/            # influxService.js, predictiveLightService.js, telegramService.js
│       ├── config.js
│       └── index.js
├── predictive-light/           # Predictive service (Python/FastAPI)
│   ├── App.py
│   └── requirements.txt
├── grafana/                    # Automatic provisioning and dashboard
│   ├── datasources/             # influxdb.yml
│   └── dashboards/              # dashboard.yml, museumguard.json
├── influxdb/
│   └── init/                   # 01-setup.sh — bucket retention
├── models/                     # STL files for 3D-printed cases
├── schematics/                 # Fritzing circuit diagrams (.fzz/.png)
├── docker-compose.yml           # Full environment orchestration
├── env.example                  # Environment variables template
├── LICENSE
└── README.md
```

---

## Interface Specification

### ESP-SEN (CoAP — port `5683/udp`)

| Resource | Method | Body / Payload | Description |
| --- | --- | --- | --- |
| `/light` | `GET` | — | Detected ambient light percentage (0–100) |
| `/accel` | `GET` | — | Average acceleration JSON `{"ax": float, "ay": float, "az": float}` |
| `/events` | `GET` (observable) | — | Latest event (`impact` / `theft` / `position`); notifies observers via CoAP Observe |
| `/thresholds` | `GET` | — | Current thresholds `{"impact": float, "theft_displacement": float}` |
| `/thresholds/impact` | `PUT` | text, numeric value (0.05–5.00) | Updates the impact threshold; `4.00 BAD_REQUEST` if out of range or payload invalid |
| `/thresholds/theft` | `PUT` | text, numeric value (0.05–5.00) | Updates the theft displacement threshold; same validation |
| `/reset_alarm` | `PUT` | — | Recalibrates the accelerometer baseline and stops GPS tracking |

### ESP-ACT (HTTP — port `80` on the real device / `8081` on the Docker mock)

| Endpoint | Method | Body | Description |
| --- | --- | --- | --- |
| `/` | `GET` | — | Basic health check |
| `/state` | `GET` | — | Current state `{"id", "brightness", "alarmState"}` |
| `/ambientlight` | `POST` | `{"brightness": 0-100}` | Sets the PWM brightness of the artwork LED |
| `/impact` | `POST` | — | Triggers impact signaling (20s blink) |
| `/theft` | `POST` | — | Triggers theft signaling (LED steady on) |
| `/reset` | `POST` | — | Resets the alarm state to `IDLE` |

### Internal Mash-up API (HTTP — port `3001`)

Used by Grafana (form panels) so they don't need to talk directly to the WoT Controller.

| Endpoint | Method | Body | Description |
| --- | --- | --- | --- |
| `/api/health` | `GET` | — | Health check |
| `/api/resetalarm` | `POST` | — | Invokes `resetAlarmLight` on the actuator |
| `/api/thresholds` | `GET` | — | Reads current thresholds from the sensor (pre-fills Grafana forms) |
| `/api/thresholds/impact` | `POST` | `{"value": number}` | Sets the impact threshold via the `sensor` Thing |
| `/api/thresholds/theft` | `POST` | `{"value": number}` | Sets the theft threshold via the `sensor` Thing |

### WoT Controller — Thing Descriptions (HTTP — port `8080`)

| Resource | Description |
| --- | --- |
| `/sensor` | Thing Description of the sensing node (properties, actions, events listed above) |
| `/actuator` | Thing Description of the actuation node |

### Predictive Light Service (HTTP — port `8000`, reachable only within the internal Docker network)

| Endpoint | Method | Description |
| --- | --- | --- |
| `/predict` | `GET` | `{"brightness": <float>}`, read from the forecast cache; `503` if no fit is available yet |
| `/health` | `GET` | Diagnostics: last fit, current EMA bias, forecast cache size/age |

---

## Getting Started

### Prerequisites

* **Docker** and **Docker Compose**
* **Node.js** (v18+) and **Python** (3.11+) *(only for standalone execution of the mocks, without Docker)*
* **ESP-IDF v5.x** *(only for deployment on real hardware)*
* Telegram Bot Token (obtainable via [@BotFather](https://t.me/BotFather))

---

### Running with Docker

The system supports several startup modes, selectable via **Docker Compose profiles** and an environment variables file.

#### Configuration

The repository only versions the `env.example` template; copy it according to the scenario you need (note: these names do **not** have a leading dot, unlike the more common `.env.*` convention):

```bash
cp env.example env.mock    # to work without hardware (both mocked)
cp env.example env.real    # to work with both real ESP32s
```

In the real-hardware scenario, set the device IP addresses on your LAN in `env.real`:

```env
ESP_SEN_ADDRESS=192.168.x.x
ESP_ACT_ADDRESS=192.168.x.x
```

> ⚠️ The PC running `docker compose` must be on the same WiFi network as the ESP32s — the WoT Controller reaches them as an outgoing CoAP/HTTP client, no port needs to be exposed on the ESP32 side.

For **mixed** scenarios (one real node, the other mocked), the only difference is that in `ESP_SEN_ADDRESS`/`ESP_ACT_ADDRESS` you put the mock container name for the node you want to simulate, and the real IP for the other:

```env
# example: ESP-SEN real device connected, ESP-ACT still mocked
ESP_SEN_ADDRESS=192.168.1.42
ESP_ACT_ADDRESS=esp-act-mock
```

#### Main Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `INFLUXDB_USERNAME` / `INFLUXDB_PASSWORD` | — | InfluxDB admin credentials (initial setup) |
| `INFLUXDB_ORG` / `INFLUXDB_BUCKET` / `INFLUXDB_TOKEN` | — | InfluxDB organization, bucket, and token, shared by all services |
| `INFLUXDB_RETENTION` | `30d` | Bucket retention, applied by `01-setup.sh` |
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | — | Grafana admin credentials |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_BOT_CHAT_ID` | — | Telegram bot; if absent, alerts remain disabled |
| `ESP_SEN_ADDRESS` / `ESP_ACT_ADDRESS` | `esp-sen-mock` / `esp-act-mock` | Node hosts (mock or real IP on the LAN) |
| `COAP_PORT` / `HTTP_PORT` | — | Ports used by the WoT Controller to reach ESP-SEN/ESP-ACT |
| `TELEMETRY_POLL_MS` | `5000` | Req/res polling interval of sensor+actuator in the Mash-up |
| `MASHUP_SERVER_API_PORT` | `3001` | Port of the Mash-up's internal REST API |
| `PREDICTIVE_LIGHT_URL` | `http://predictive-light:8000` | URL of the predictive service, as seen by the Mash-up |
| `PREDICTIVE_LIGHT_TIMEOUT_MS` | `3000` | Timeout for the call to `predictive-light` before falling back to the reactive rule |
| `PREDICT_INTERVAL_S` | `60` | How often (seconds) `predictive-light` re-fits the ARIMA model and regenerates the forecast cache ("refit-once, serve-many") |
| `FORECAST_STEP_S` | `1` | Granularity (in seconds) of the forecasts served from the cache between one refit and the next; ideally aligned with `TELEMETRY_POLL_MS` |
| `PREDICTION_HORIZON_S` | `30` | Forecast horizon used for reconciliation/EMA bias (seconds into the future relative to the fit) |
| `HISTORY_WINDOW_S` | `600` | Rolling history window used for the ARIMA fit |
| `EMA_ALPHA` | `0.3` | EMA weight for the adaptive bias correction |

#### Clone and Run

```bash
git clone https://github.com/your-username/museum-guard.git
cd museum-guard
```

**With both mocks (test environment, no hardware):**
```bash
docker compose --env-file env.mock --profile mock up -d
```

**With a single mocked node** (the other real, IP address set in `env.mock`/`env.real`):
```bash
docker compose --env-file env.mock --profile mock-sen up -d   # only ESP-SEN mocked
docker compose --env-file env.mock --profile mock-act up -d   # only ESP-ACT mocked
```

**With real hardware connected (both nodes):**
```bash
docker compose --env-file env.real up -d
```
(here the mock containers do *not* start, even though they remain defined in `docker-compose.yml`, because they don't belong to the default profile)

If you'd rather not type `--env-file` every time, you can copy the chosen file to `.env` (which Compose loads automatically):
```bash
cp env.mock .env   # or env.real, depending on the scenario
docker compose --profile mock up -d          # both mocked
docker compose --profile mock-sen up -d      # only sensing mocked
docker compose up -d                         # real hardware, no profile
```

#### Check Running Services

* **Grafana:** [http://localhost:3000](http://localhost:3000) — dashboard and InfluxDB datasource already provisioned
* **InfluxDB:** [http://localhost:8086](http://localhost:8086)
* **WoT Controller:** [http://localhost:8080](http://localhost:8080) — Thing Descriptions of the exposed nodes at `/sensor` and `/actuator`
* **Mash-up API:** [http://localhost:3001/api/health](http://localhost:3001/api/health)
* **Predictive Light:** reachable only within the internal Docker network (`http://predictive-light:8000`), not exposed on the host
* **ESP-SEN Mock** *(`mock` or `mock-sen` profiles)*: `coap://localhost:5683`
* **ESP-ACT Mock** *(`mock` or `mock-act` profiles)*: [http://localhost:8081](http://localhost:8081)

#### Stopping the Services

```bash
docker compose down
```

### Standalone Execution of the Mocks (without Docker)

Useful for developing or debugging a single mock in isolation:

```bash
# Sensing mock (ESP-SEN) — Python
cd esp/esp-sen/mockup
pip install -r requirements.txt
python esp_sen_mock.py

# Actuation mock (ESP-ACT) — Node.js
cd esp/esp-act/mockup
npm install
node server.js
```

---

## License

This project is distributed under the **MIT** license. See the [LICENSE](./LICENSE) file for details.
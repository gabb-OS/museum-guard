# MuseumGuard

**W3C WoT-based Smart Artwork Protection System**
*Progetto per il corso di Internet of Things (A.A. 2025-2026) — Università di Bologna (Unibo)*

---

## Descrizione del Progetto

**MuseumGuard** è un sistema IoT distribuito progettato per il monitoraggio continuo e la protezione attiva di opere d'arte in ambienti museali. Il sistema combina monitoraggio ambientale, rilevamento tempestivo di minacce (urti accidentali e tentativi di furto), controllo illuminotecnico adattivo e interoperabilità basata sullo standard **W3C Web of Things (WoT)**.

### Architettura Generale

```text
  +------------------+        CoAP       +--------------------+
  | ESP-SEN (Sensing)| ----------------> |                    |
  +------------------+                   |   WoT Controller   |
  +------------------+        HTTP       |                    |
  | ESP-ACT (Actuat.)| <-----------------+---------+----------+
  +------------------+                             |
                                                   v
                                         +----------------------+
                                         |  Mash-up Application |
                                         +----+---------+---+---+
                                              |         |   |
                             +----------------+         |   +------------+
                             |                          |                |
                             v                          v                v
                       +----------+           +--------------------+ +------------+
                       | InfluxDB | <-------> | predictive-light   | | Telegram   |
                       +----+-----+           | (Python/FastAPI)   | | Alert Bot  |
                            |                 +--------------------+ +------------+
                            v
                       +----------+
                       | Grafana  |
                       +----------+
```

I nodi sensoristici ed attuativi non comunicano mai direttamente tra loro: l'orchestrazione dei flussi e della logica applicativa avviene tramite il **Controller WoT** e l'applicazione **Mash-up**. Il servizio `predictive-light` legge lo storico da InfluxDB e viene interrogato dal Mash-up solo per ottenere la stima di luminosità: non parla mai direttamente con i dispositivi.

---

## Componenti del Sistema

### 1. ESP-SEN — Nodo di Sensing

* **Firmware:** ESP-IDF con task FreeRTOS dedicati ad acquisizione e trasmissione (`esp/esp-sen/sensing`).
* **Hardware:** ESP32, sensore di luce ambientale (fotoresistore su ADC), accelerometro I2C a 3 assi (MPU6050), modulo GPS UART.
* **Funzionalità:**
  * Lettura periodica dell'intensità luminosa e dell'accelerazione triassiale.
  * Rilevamento **urti accidentali** (variazione brusca sull'asse Z rispetto al campione precedente).
  * Rilevamento **furti** (spostamento sostenuto sull'asse X rispetto a una baseline, confermato su più campioni consecutivi con cooldown anti-rimbalzo).
  * Tracking GPS attivo automaticamente dopo un furto confermato, finché non arriva un reset.
  * Configurazione dinamica delle soglie di urto/furto a runtime, senza ricompilazione.
* **Protocollo:** CoAP (Constrained Application Protocol), porta UDP `5683`.
* **Mock:** Python (`esp/esp-sen/mockup/esp_sen_mock.py`), stessa interfaccia CoAP del firmware reale.

### 2. ESP-ACT — Nodo di Attuazione

* **Firmware:** ESP-IDF con task FreeRTOS per gestione LED ed endpoint HTTP (`esp/esp-act/actuating`).
* **Hardware:** ESP32, LED PWM (illuminazione opera, dimmerabile via `ledc`), LED digitale dedicato a urto/furto.
* **Funzionalità:**
  * Controllo adattivo dell'illuminazione dell'opera tramite dimming PWM (0–100%).
  * **Segnalazione Urto:** lampeggio del LED d'allarme per 20 secondi (timer software), interrompibile da un evento di furto.
  * **Segnalazione Furto:** accensione permanente del LED d'allarme fino a reset manuale.
* **Protocollo:** HTTP, porta `80` sul dispositivo reale (esposta come `8081` sul mock via Docker).
* **Mock:** Node.js (`esp/esp-act/mockup/server.js`, `mockup-act.js`), stessa interfaccia HTTP del firmware reale.

### 3. WoT Controller (PC)

Applicazione Node.js (`wot-controller/`) che astrae i due dispositivi fisici esponendoli come **W3C WoT Things** standardizzati via `@node-wot`, raggiungibile su `http://localhost:8080`:

* **Thing `sensor`** (`/sensor`) — espone ESP-SEN:
  * *Properties:* `ambientLight`, `accelerometer` (`{ax, ay, az}`), `thresholds` (`{impact, theft_displacement}`) — tutte osservabili.
  * *Actions:* `setImpactThreshold`, `setTheftThreshold`, `resetTracking`.
  * *Events:* `alarmEvent` — notifica `impact` / `theft` / `position`.
* **Thing `actuator`** (`/actuator`) — espone ESP-ACT:
  * *Properties:* `artworkLedBrightness`, `alarmLightState` (`IDLE`/`IMPACT`/`THEFT`) — osservabili.
  * *Actions:* `regulateBrightness`, `triggerImpactBlink`, `triggerTheftAlarm`, `resetAlarmLight`.

Gli adapter verso i dispositivi fisici (CoAP per il sensore, HTTP per l'attuatore) vivono in `wot-controller/src/adapters/`.

### 4. Mash-up Application (PC)

Rappresenta il cuore logico del sistema (`mashup-app/`):

* Consuma le Thing esposte dal Controller WoT (`clients/wotConsumer.js`).
* Effettua polling periodico req/res di sensore + attuatore e persiste telemetria, eventi e soglie su **InfluxDB** (`logic/telemetryPoller.js`, `services/influxService.js`).
* Sottoscrive l'evento `alarmEvent` in modalità pub/sub e reagisce ad urti/furti attivando gli attuatori corrispondenti e inviando notifiche Telegram (`logic/alarmHandler.js`).
* Interroga il servizio `predictive-light` per la luminosità target, con fallback reattivo esplicito in caso di errore/timeout (`services/predictiveLightService.js`).
* Espone una piccola REST API interna (`api/server.js`, porta `3001`) usata dai pannelli form di Grafana per azioni come reset allarme e aggiornamento soglie, senza che Grafana debba parlare direttamente col Controller WoT.

### 5. Predictive Lighting Service

Microservizio Python/FastAPI separato (`predictive-light/App.py`), containerizzato a parte:

* Rifitta un modello **ARIMA** (`pmdarima`) sulla finestra rolling di `ambient_light` letta da InfluxDB (`HISTORY_WINDOW_S` secondi), ogni `PREDICT_INTERVAL_S` secondi.
* Applica una **bias correction adattiva**: mantiene una EMA (`EMA_ALPHA`) sugli errori delle previsioni passate, confrontando ogni previsione scaduta con il valore reale osservato dopo (`prediction_error`, measurement dedicata).
* Espone `GET /predict` → `{"brightness": <float>}` (target LED, complementare alla luce ambientale prevista) e `GET /health` per diagnostica; risponde `503` finché non ha ancora un fit valido, così il Mash-up passa al fallback reattivo.
* Scrive su InfluxDB sia le previsioni (`predicted_light`) sia gli errori di riconciliazione (`prediction_error`), entrambe visualizzabili in Grafana.

> Nota di design: il fit/riconciliazione lavorano sempre nello spazio "luce ambientale prevista" (comparabile 1:1 con `ambient_light` reale nel pannello Grafana dedicato); solo il valore restituito da `/predict` viene convertito nel target di luminosità artificiale (relazione inversa: più luce ambientale prevista → meno luce artificiale serve).

### 6. Data Storage & Visualizzazione

* **InfluxDB 2.7:** Time-series database per telemetria (`ambient_light`, `acceleration`, `actuator_state`), eventi (`impact_event`, `theft_event`, `position`), soglie (`device_thresholds`) e dati predittivi (`predicted_light`, `prediction_error`). Retention del bucket impostata a `INFLUXDB_RETENTION` (default `30d`) da `influxdb/init/01-setup.sh` al primo avvio.
* **Grafana:** Dashboard `MuseumGuard` provisionata automaticamente (`grafana/dashboards/museumguard.json`) con grafici ambientali, tabelle eventi urto/furto, mappa GPS, stato attuatore, confronto luce prevista/reale, errore di predizione, e pannelli form (plugin `volkovlabs-form-panel`) per reset allarme e impostazione soglie — questi ultimi chiamano direttamente le API del Mash-up (`http://localhost:3001/api/...`).

### 7. Telegram Alert Bot

Modulo `services/telegramService.js` nel Mash-up: invia notifiche di testo fire-and-forget alla chat configurata quando `alarmHandler.js` riceve un evento `impact` o `theft`. Se `TELEGRAM_BOT_TOKEN`/`TELEGRAM_BOT_CHAT_ID` non sono impostati, il bot resta disattivato senza bloccare il resto del sistema.

---

## Funzionalità Bonus Implementate

* **Soglie di Rilevamento Configurabili:** aggiornamento a runtime delle soglie di urto (`thresholds/impact`) e furto (`thresholds/theft`) tramite il WoT Controller, senza ricompilazione del firmware, con range validato lato firmware (`THRESHOLD_MIN`–`THRESHOLD_MAX`, 0.05–5.00 g).
* **Controllo Predittivo dell'Illuminazione:** modello ARIMA con bias correction adattiva che stima la luce ambientale nei successivi `PREDICTION_HORIZON_S` secondi e regola proattivamente il LED PWM, con fallback reattivo automatico in caso di indisponibilità del servizio predittivo.
* **Telegram Alert Bot:** notifiche istantanee in caso di emergenza (urto/furto).

---

## Struttura del Repository

```text
museum-guard/
├── esp/
│   ├── esp-act/
│   │   ├── actuating/         # Firmware ESP-IDF (Nodo Attuazione)
│   │   │   └── main/          # main.c, networkConnect.c, shared.h
│   │   └── mockup/            # Mock Node.js (server.js, mockup-act.js)
│   └── esp-sen/
│       ├── sensing/           # Firmware ESP-IDF (Nodo Sensing)
│       │   └── main/
│       │       └── mylib/     # accelerometer, gps, wifi, coap_server
│       └── mockup/            # Mock Python (esp_sen_mock.py)
├── wot-controller/            # W3C WoT Controller (Node.js / node-wot)
│   ├── src/
│   │   ├── adapters/          # espAct-adapter.js, espSen-adapter.js
│   │   ├── coap/              # coap-client.js
│   │   ├── things/            # espActThing.js, espSenThing.js
│   │   └── index.js
│   └── mockup/                # script di test (testActThing.js)
├── mashup-app/                # Mash-up Application (Node.js)
│   └── src/
│       ├── api/                # server.js — REST API interna (porta 3001)
│       ├── clients/             # wotConsumer.js
│       ├── logic/               # telemetryPoller.js, alarmHandler.js
│       ├── services/            # influxService.js, predictiveLightService.js, telegramService.js
│       ├── config.js
│       └── index.js
├── predictive-light/           # Servizio predittivo (Python/FastAPI)
│   ├── App.py
│   └── requirements.txt
├── grafana/                    # Provisioning automatico e dashboard
│   ├── datasources/             # influxdb.yml
│   └── dashboards/              # dashboard.yml, museumguard.json
├── influxdb/
│   └── init/                   # 01-setup.sh — retention bucket
├── models/                     # File STL per case stampati in 3D
├── schematics/                 # Schemi elettrici Fritzing (.fzz/.png)
├── docker-compose.yml           # Orchestrazione completa dell'ambiente
├── env.example                  # Template variabili d'ambiente
├── LICENSE
└── README.md
```

---

## Specifica delle Interfacce

### ESP-SEN (CoAP — porta `5683/udp`)

| Risorsa | Metodo | Body / Payload | Descrizione |
| --- | --- | --- | --- |
| `/light` | `GET` | — | Percentuale di luce ambientale rilevata (0–100) |
| `/accel` | `GET` | — | Accelerazione media JSON `{"ax": float, "ay": float, "az": float}` |
| `/events` | `GET` (osservabile) | — | Ultimo evento (`impact` / `theft` / `position`); notifica gli observer via CoAP Observe |
| `/thresholds` | `GET` | — | Soglie correnti `{"impact": float, "theft_displacement": float}` |
| `/thresholds/impact` | `PUT` | testo, valore numerico (0.05–5.00) | Aggiorna la soglia di urto; `4.00 BAD_REQUEST` se fuori range o payload non valido |
| `/thresholds/theft` | `PUT` | testo, valore numerico (0.05–5.00) | Aggiorna la soglia di spostamento per il furto; stessa validazione |
| `/reset_alarm` | `PUT` | — | Ricalibra la baseline dell'accelerometro e ferma il tracking GPS |

### ESP-ACT (HTTP — porta `80` su dispositivo reale / `8081` sul mock Docker)

| Endpoint | Metodo | Body | Descrizione |
| --- | --- | --- | --- |
| `/` | `GET` | — | Health check base |
| `/state` | `GET` | — | Stato corrente `{"id", "brightness", "alarmState"}` |
| `/ambientlight` | `POST` | `{"brightness": 0-100}` | Regola la luminosità PWM del LED dell'opera |
| `/impact` | `POST` | — | Attiva la segnalazione urto (lampeggio 20s) |
| `/theft` | `POST` | — | Attiva la segnalazione furto (LED fisso acceso) |
| `/reset` | `POST` | — | Reset dello stato d'allarme a `IDLE` |

### Mash-up API interna (HTTP — porta `3001`)

Usata da Grafana (pannelli form) per non dover parlare direttamente col Controller WoT.

| Endpoint | Metodo | Body | Descrizione |
| --- | --- | --- | --- |
| `/api/health` | `GET` | — | Health check |
| `/api/resetalarm` | `POST` | — | Invoca `resetAlarmLight` sull'attuatore |
| `/api/thresholds` | `GET` | — | Legge le soglie correnti dal sensore (precompila i form Grafana) |
| `/api/thresholds/impact` | `POST` | `{"value": number}` | Imposta la soglia di urto via Thing `sensor` |
| `/api/thresholds/theft` | `POST` | `{"value": number}` | Imposta la soglia di furto via Thing `sensor` |

### WoT Controller — Thing Descriptions (HTTP — porta `8080`)

| Risorsa | Descrizione |
| --- | --- |
| `/sensor` | Thing Description del nodo di sensing (properties, actions, events elencati sopra) |
| `/actuator` | Thing Description del nodo di attuazione |

---

## Guida all'Avvio

### Requisiti Preliminari

* **Docker** e **Docker Compose**
* **Node.js** (v18+) e **Python** (3.11+) *(solo per esecuzione standalone dei mockup, senza Docker)*
* **ESP-IDF v5.x** *(solo per deployment su hardware reale)*
* Token Bot Telegram (ottenibile tramite [@BotFather](https://t.me/BotFather))

---

### Avvio con Docker

Il sistema supporta più modalità di avvio, selezionabili tramite i **profili di Docker Compose** e un file di variabili d'ambiente.

#### Configurazione

Il repository versiona solo il template `env.example`; copialo secondo lo scenario che ti serve (i nomi qui **non** hanno il punto iniziale, a differenza delle convenzioni `.env.*` più comuni):

```bash
cp env.example env.mock    # per lavorare senza hardware (entrambi mockati)
cp env.example env.real    # per lavorare con entrambi gli ESP32 reali
```

Nello scenario reale, imposta in `env.real` gli indirizzi IP dei dispositivi sulla tua LAN:

```env
ESP_SEN_ADDRESS=192.168.x.x
ESP_ACT_ADDRESS=192.168.x.x
```

> ⚠️ Il PC che esegue `docker compose` deve trovarsi sulla stessa rete WiFi degli ESP32 — il WoT Controller li raggiunge come client CoAP/HTTP in uscita, non serve nessuna porta esposta lato ESP32.

Per gli scenari **misti** (un nodo reale, l'altro mockato), l'unica differenza è che in `ESP_SEN_ADDRESS`/`ESP_ACT_ADDRESS` metti il nome del container mock per il nodo che vuoi simulare e l'IP reale per l'altro:

```env
# esempio: ESP-SEN reale collegato, ESP-ACT ancora mockato
ESP_SEN_ADDRESS=192.168.1.42
ESP_ACT_ADDRESS=esp-act-mock
```

#### Variabili d'ambiente principali

| Variabile | Default | Descrizione |
| --- | --- | --- |
| `INFLUXDB_USERNAME` / `INFLUXDB_PASSWORD` | — | Credenziali admin InfluxDB (setup iniziale) |
| `INFLUXDB_ORG` / `INFLUXDB_BUCKET` / `INFLUXDB_TOKEN` | — | Organizzazione, bucket e token InfluxDB, condivisi da tutti i servizi |
| `INFLUXDB_RETENTION` | `30d` | Retention del bucket, applicata da `01-setup.sh` |
| `GRAFANA_USER` / `GRAFANA_PASSWORD` | — | Credenziali admin Grafana |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_BOT_CHAT_ID` | — | Bot Telegram; se assenti, gli alert restano disattivati |
| `ESP_SEN_ADDRESS` / `ESP_ACT_ADDRESS` | `esp-sen-mock` / `esp-act-mock` | Host dei nodi (mock o IP reale sulla LAN) |
| `COAP_PORT` / `HTTP_PORT` | — | Porte con cui il WoT Controller raggiunge ESP-SEN/ESP-ACT |
| `TELEMETRY_POLL_MS` | `5000` | Intervallo di polling req/res sensore+attuatore nel Mash-up |
| `MASHUP_SERVER_API_PORT` | `3001` | Porta della REST API interna del Mash-up |
| `PREDICTIVE_LIGHT_URL` | `http://predictive-light:8000` | URL del servizio predittivo, visto dal Mash-up |
| `PREDICTIVE_LIGHT_TIMEOUT_MS` | `3000` | Timeout della chiamata a `predictive-light` prima del fallback reattivo |
| `PREDICT_INTERVAL_S` | `5` | Ogni quanti secondi `predictive-light` rifitta il modello |
| `PREDICTION_HORIZON_S` | `30` | Orizzonte di previsione (secondi nel futuro) |
| `HISTORY_WINDOW_S` | `600` | Finestra rolling di storico usata per il fit ARIMA |
| `EMA_ALPHA` | `0.3` | Peso della EMA per la bias correction adattiva |

#### Clona ed avvia

```bash
git clone https://github.com/tuo-username/museum-guard.git
cd museum-guard
```

**Con entrambi i mockup (ambiente di test, senza hardware):**
```bash
docker compose --env-file env.mock --profile mock up -d
```

**Con un solo nodo mockato** (l'altro reale, indirizzo IP impostato in `env.mock`/`env.real`):
```bash
docker compose --env-file env.mock --profile mock-sen up -d   # solo ESP-SEN mockato
docker compose --env-file env.mock --profile mock-act up -d   # solo ESP-ACT mockato
```

**Con l'hardware reale collegato (entrambi i nodi):**
```bash
docker compose --env-file env.real up -d
```
(qui i container mock *non* partono, anche restando definiti nel `docker-compose.yml`, perché non appartengono al profilo di default)

Se preferisci non scrivere `--env-file` ad ogni comando, puoi copiare il file scelto su `.env` (che Compose carica automaticamente):
```bash
cp env.mock .env   # oppure env.real, a seconda dello scenario
docker compose --profile mock up -d          # entrambi mockati
docker compose --profile mock-sen up -d      # solo sensing mockato
docker compose up -d                         # hardware reale, nessun profilo
```

#### Verifica i servizi attivi

* **Grafana:** [http://localhost:3000](http://localhost:3000) — dashboard e datasource InfluxDB già provisionati
* **InfluxDB:** [http://localhost:8086](http://localhost:8086)
* **WoT Controller:** [http://localhost:8080](http://localhost:8080) — Thing Description dei nodi esposti su `/sensor` e `/actuator`
* **Mash-up API:** [http://localhost:3001/api/health](http://localhost:3001/api/health)
* **Predictive Light:** raggiungibile solo internamente alla rete Docker (`http://predictive-light:8000`), non esposto sull'host
* **ESP-SEN Mock** *(profili `mock` o `mock-sen`)*: `coap://localhost:5683`
* **ESP-ACT Mock** *(profili `mock` o `mock-act`)*: [http://localhost:8081](http://localhost:8081)

#### Arresto dei servizi

```bash
docker compose down
```

### Esecuzione Standalone dei Mockup (senza Docker)

Utile per sviluppare o debuggare un singolo mockup in isolamento:

```bash
# Mockup Sensing (ESP-SEN) — Python
cd esp/esp-sen/mockup
pip install -r requirements.txt
python esp_sen_mock.py

# Mockup Attuazione (ESP-ACT) — Node.js
cd esp/esp-act/mockup
npm install
node server.js
```

---

## Licenza

Questo progetto è distribuito sotto licenza **MIT**. Consulta il file [LICENSE](./LICENSE) per ulteriori dettagli.

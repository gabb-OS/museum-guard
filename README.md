# MuseumGuard

**W3C WoT-based Smart Artwork Protection System**  
*Progetto per il corso di Internet of Things (A.A. 2025-2026) — Università di Bologna (Unibo)*

---

##  Descrizione del Progetto

**MuseumGuard** è un sistema IoT distribuito progettato per il monitoraggio continuo e la protezione attiva di opere d'arte in ambienti museali. Il sistema combina monitoraggio ambientale, rilevamento tempestivo di minacce (urti accidentali e tentativi di furto), controllo illuminotecnico adattivo e interoperabilità basata sullo standard **W3C Web of Things (WoT)**.

### Architettura Generale

```text
  +------------------+       CoAP        +--------------------+
  | ESP-SEN (Sensing)| ----------------> |                    |
  +------------------+                   |   WoT Controller   |
                                         |    (PC Application)|
  +------------------+       HTTP        |                    |
  | ESP-ACT (Actuat.)| <---------------- +---------+----------+
  +------------------+                             |
                                                   v
                                         +--------------------+
                                         | Mash-up Application|
                                         +----+---------+-----+
                                              |         |
                                +-------------+         +-------------+
                                v                                     v
                          +----------+                          +------------+
                          | InfluxDB |                          | Telegram   |
                          +----+-----+                          | Alert Bot  |
                               |                                +------------+
                               v
                          +----------+
                          | Grafana  |
                          +----------+

```

I nodi sensoristici ed attuativi non comunicano mai direttamente tra loro: l'orchestrazione dei flussi e della logica applicativa avviene tramite il **Controller WoT** e l'applicazione **Mash-up**.

---

## Componenti del Sistema

### 1. ESP-SEN — Nodo di Sensing

* **Firmware:** ESP-IDF con task FreeRTOS dedicati ad acquisizione e trasmissione.
* **Hardware:** ESP32, sensore di luce ambientale, accelerometro a 3 assi (X, Y, Z).
* **Funzionalità:**
* Lettura periodica dell'intensità luminosa e dell'accelerazione triassiale.
* Rilevamento **urti accidentali** (accelerazioni brusche lungo l'asse longitudinale).
* Rilevamento **furti** (spostamenti verticali significativi).
* Configurazione dinamica delle soglie a runtime.


* **Protocollo:** CoAP (Constrained Application Protocol).

### 2. ESP-ACT — Nodo di Attuazione

* **Firmware:** ESP-IDF con task FreeRTOS per gestione LED ed endpoint HTTP.
* **Hardware:** ESP32, LED PWM (illuminazione opera), LED fisso (segnalazione allarmi).
* **Funzionalità:**
* Controllo adattivo dell'illuminazione dell'opera tramite dimming PWM.
* **Segnalazione Urto:** lampeggio del LED d'allarme per 20 secondi.
* **Segnalazione Furto:** accensione permanente del LED d'allarme fino a reset manuale.


* **Protocollo:** HTTP.

### 3. WoT Controller (PC)

Applicazione middleware che astrae i dispositivi fisici esponendoli come **W3C WoT Things** standardizzati:

* **Properties:** Misure di luce, accelerazione X/Y/Z, stato attuale degli attuatori.
* **Actions:** Regolazione fari, attivazione allarmi, aggiornamento soglie.
* **Events:** Notifiche asincrone di urto e furto.

### 4. Mash-up Application (PC)

Rappresenta il cuore logico del sistema:

* Consuma le Thing esposte dal Controller WoT.
* Persiste i dati sensoriali e lo storico degli eventi su **InfluxDB**.
* Esegue la logica di feedback adattivo e attiva automaticamente gli attuatori su **ESP-ACT**.
* Invia le notifiche critiche tramite il **Telegram Bot**.

### 5. Data Storage & Visualizzazione

* **InfluxDB:** Time-series database per la memorizzazione di telemetria, eventi e stati di attuazione.
* **Grafana:** Dashboard di monitoraggio in tempo reale con grafici ambientali, registro eventi e stime predittive.

---

## Funzionalità Bonus Implementate

* **Soglie di Rilevamento Configurabili:** Aggiornamento a runtime delle soglie di urto e furto tramite il WoT Controller senza ricompilazione del firmware.
*  **Controllo Predittivo dell'Illuminazione:** Algoritmo predittivo che stima la luce ambientale nei successivi Y secondi e regola proattivamente il LED PWM.
*  **Telegram Alert Bot:** Notifiche istantanee in caso di emergenza ed interfaccia CLI via chat per interrogare lo stato dell'opera d'arte.

---

##  Struttura del Repository

```text
museum-guard/
├── esp/
│   ├── esp-sen/              
│   │   ├── sensing/          # Firmware ESP-IDF (Nodo Sensing)
│   │   └── mockup/           # Mockup Python per test senza hardware
│   └── esp-act/             
│       ├── actuating/        # Firmware ESP-IDF (Nodo Attuazione)
│       └── mockup/           # Mockup Python per test senza hardware
├── wot-controller/       # W3C WoT Controller (Node.js/Python)
├── grafana/              # Provisioning automatico e dashboard JSON
│   ├── datasources/      # Configurazione automatica InfluxDB
│   └── dashboards/       # Layout dashboard MuseumGuard
├── influxdb/
│   └── init/             # Script di inizializzazione bucket
├── docker-compose.yml    # Orchestrazione completa dell'ambiente
├── LICENSE
└── README.md

```

---

## 🔌 Specifica delle Interfacce Mockup / Nodi

### ESP-SEN (CoAP - Porta `5683/udp`)

| Risorsa | Metodo | Descrizione |
| --- | --- | --- |
| `/light` | `GET` | Misura corrente illuminazione |
| `/accel` | `GET` | Valori accelerazione JSON `{"x": float, "y": float, "z": float}` |
| `/events` | `GET` | Coda eventi generati (`IMPACT`, `THEFT`) |
| `/thresholds` | `GET / PUT` | Lettura e modifica soglie di allarme |

### ESP-ACT (HTTP - Porta `8081`)

| Endpoint | Metodo | Body / Query | Descrizione |
| --- | --- | --- | --- |
| `/light/intensity` | `POST` | `{"value": 0-100}` | Regola la luminosità PWM del LED |
| `/led/fixed` | `POST` | `{"command": "ON|OFF|BLINK", "duration": 20}` | Controlla il LED di allarme |
| `/state` | `GET` | — | Restituisce lo stato attuale degli attuatori |

---

## 🚀 Guida all'Avvio

### Requisiti Preliminari

* **Docker** e **Docker Compose**
* **Node.js** (v18+) o **Python** (3.11+)
* **ESP-IDF v5.x** *(solo per deployment su hardware reale)*
* Token Bot Telegram (ottenibile tramite [@BotFather](https://t.me/BotFather))

---

### Avvio Rapido con Docker (Ambiente di Test)

È possibile avviare l'intera infrastruttura (InfluxDB, Grafana e i Mockup dei nodi) in ambiente simulato senza hardware fisico:

1. **Clona il repository ed avvia i container:**
```bash
git clone [https://github.com/tuo-username/museum-guard.git](https://github.com/tuo-username/museum-guard.git)
cd museum-guard
docker-compose up -d

```


2. **Verifica i servizi attivi:**
* **Grafana:** [http://localhost:3000](http://localhost:3000) *(Credenziali: `admin` / `admin`)* — *Dashboard ed InfluxDB già configurati via provisioning.*
* **InfluxDB:** [http://localhost:8086](http://localhost:8086)
* **ESP-SEN Mock:** `coap://localhost:5683`
* **ESP-ACT Mock:** `http://localhost:8081`


3. **Arresto dei servizi:**
```bash
docker-compose down

```



---

### Esecuzione Standalone dei Mockup (Senza Docker)

Per testare o sviluppare unicamente i mockup in Python:

```bash
# Avvio Mockup Sensing (ESP-SEN)
cd esp/mockup/sen
pip install -r requirements.txt
python esp_sen_mock.py

# Avvio Mockup Attuazione (ESP-ACT)
cd esp/mockup/act
pip install -r requirements.txt
python esp_act_mock.py

```

---

## 📄 Licenza

Questo progetto è distribuito sotto licenza **MIT**. Consulta il file [LICENSE](https://www.google.com/search?q=LICENSE) per ulteriori dettagli.

# MuseumGuard

**W3C WoT-based Smart Artwork Protection System**
Progetto per il corso di Internet of Things, a.a. 2025-2026 @ Università di Bologna (unibo)

## Descrizione

MuseumGuard è un sistema IoT distribuito per il monitoraggio e la protezione di opere d'arte in ambienti museali. Il sistema integra sensoristica ambientale, rilevamento di eventi (urti accidentali e furti), controllo adattivo dell'illuminazione e interoperabilità tramite lo standard W3C Web of Things (WoT).

L'architettura è composta da due dispositivi embedded basati su ESP32, un controller WoT eseguito su PC che espone le funzionalità dei dispositivi come Thing interoperabili, e un'applicazione mash-up che implementa la logica applicativa del sistema, integrando i dati raccolti con un database time-series e una dashboard di visualizzazione.

Tutte le funzionalità richieste dalla specifica di progetto sono state implementate, incluse tutte le funzionalità bonus opzionali.

## Architettura del sistema

```
 ESP-SEN (sensing)          ESP-ACT (actuation)
 luce ambientale             LED PWM (illuminazione)
 accelerometro XYZ           LED fisso (allarme)
       |  CoAP                     |  HTTP
       v                           v
        WoT Controller (PC)
     Thing ESP-SEN | Thing ESP-ACT
                |
        Mash-up Application
                |
        +-------+-------+
        |               |
   InfluxDB         Grafana
   (time series)     (dashboard)
                |
        Telegram Alert Bot
```

### ESP-SEN – Nodo di sensing
- Firmware: ESP-IDF con task FreeRTOS dedicati per acquisizione luce, acquisizione accelerazione, rilevamento eventi e comunicazione CoAP.
- Sensori: sensore di luce ambientale, accelerometro a 3 assi (X/Y/Z).
- Funzionalità:
  - acquisizione periodica dell'intensità luminosa ambientale;
  - acquisizione periodica dei valori di accelerazione sui tre assi;
  - rilevamento di eventi di urto accidentale (spostamenti bruschi lungo l'asse longitudinale);
  - rilevamento di eventi di furto (spostamento verticale significativo del sensore);
  - soglie di rilevamento configurabili a runtime (vedi funzionalità bonus).
- Comunicazione: invio dati e notifiche verso il controller tramite protocollo CoAP.

### ESP-ACT – Nodo di attuazione
- Firmware: ESP-IDF con task FreeRTOS dedicati per la gestione dei due LED e la comunicazione HTTP.
- Attuatori: LED a intensità variabile (PWM) per la simulazione dell'illuminazione dell'opera, LED a intensità fissa per la segnalazione di allarmi.
- Funzionalità:
  - regolazione adattiva dell'intensità del LED PWM per il controllo dell'illuminazione dell'opera;
  - lampeggio del LED fisso per 20 secondi in seguito a un evento di urto accidentale;
  - accensione permanente del LED fisso in seguito a un evento di furto, fino a reset manuale da parte dell'operatore.
- Comunicazione: ricezione dei comandi di attuazione dal controller tramite protocollo HTTP.

I due nodi ESP32 non comunicano direttamente tra loro: ogni scambio di informazioni avviene attraverso il controller WoT.

### Controller WoT
Applicazione eseguita su PC che espone le funzionalità di ESP-SEN ed ESP-ACT come Thing conformi allo standard W3C WoT, tramite:
- **Properties**: misure dei sensori (luce, accelerazione) e stato degli attuatori (intensità LED PWM, stato LED fisso);
- **Actions**: attivazione/regolazione degli attuatori e comandi di controllo dell'illuminazione;
- **Events**: notifiche di urto accidentale e di furto rilevato.

### Mash-up application
Applicazione eseguita su PC che consuma i Thing esposti dal controller e implementa la logica applicativa del sistema:
- lettura periodica delle misure provenienti da ESP-SEN;
- persistenza delle misure sensoriali su InfluxDB;
- ricezione e persistenza degli eventi di urto accidentale e di furto;
- invocazione automatica delle Action su ESP-ACT per:
  - la regolazione dell'illuminazione dell'opera;
  - l'attivazione del lampeggio in seguito a un urto;
  - l'attivazione dell'allarme luminoso fisso in seguito a un furto.

### Data storage – InfluxDB
Il database time-series memorizza:
- misure di luce ambientale;
- valori di accelerazione sui tre assi;
- eventi di urto accidentale;
- eventi di furto;
- stato degli attuatori;
- valori di regolazione dell'illuminazione.

### Dashboard – Grafana
La dashboard fornisce visualizzazione in tempo reale di:
- misure di luce ambientale;
- valori di accelerazione sui tre assi;
- eventi di urto accidentale;
- eventi di furto;
- stato degli attuatori;
- intensità di illuminazione;
- previsioni delle condizioni di luce (funzionalità bonus, vedi sotto).

## Funzionalità bonus implementate

- **Configurable Detection Thresholds**: le soglie di accelerazione e di spostamento utilizzate per il rilevamento di urti accidentali e furti sono configurabili a runtime tramite il controller, senza necessità di ricompilare o riavviare il firmware.
- **Predictive Lighting Control**: il sistema predice le condizioni di luce ambientale nei successivi Y secondi e regola in modo proattivo l'intensità di illuminazione dell'opera, anticipando le variazioni della luce circostante.
- **Telegram Alert Bot**: un bot Telegram notifica in tempo reale gli utenti autorizzati in caso di eventi di urto accidentale o di furto, e consente di interrogare lo stato corrente del sistema.

## Struttura del repository

```
museum-guard/
├── esp-sen/              # Firmware ESP-IDF del nodo di sensing
├── esp-act/              # Firmware ESP-IDF del nodo di attuazione
├── wot-controller/       # Controller W3C WoT (esposizione Thing)
├── mashup-app/           # Applicazione mash-up (logica applicativa)
├── telegram-bot/         # Bot Telegram per gli alert
├── grafana/              # Dashboard e provisioning Grafana
├── docker-compose.yml    # Orchestrazione InfluxDB, Grafana, servizi
├── LICENSE
└── README.md
```

## Requisiti

- ESP-IDF (v5.x) e toolchain per la programmazione dei due ESP32
- Due schede ESP32 con sensore di luce ambientale, accelerometro a 3 assi, LED PWM e LED singolo
- Node.js o Python (a seconda dell'implementazione del controller WoT e della mash-up app)
- Docker e Docker Compose (per InfluxDB e Grafana)
- Un bot Telegram registrato tramite BotFather, con token API


## Configurazione a runtime

Tramite il controller WoT è possibile modificare a runtime:
- le soglie di accelerazione e spostamento per il rilevamento di urti e furti;
- i parametri di regolazione dell'illuminazione adattiva.

## Licenza

Distribuito con licenza MIT. Vedere il file [LICENSE](LICENSE) per i dettagli.

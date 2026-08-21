// esp-act-mock/server.js
const express = require('express');
const app = express();
const PORT = process.env.HTTP_PORT || 80;

app.use(express.json());

// Stato interno (simula quello del firmware)
let state = {
  brightness: 30,          // valore iniziale come nel firmware? (nel main parte da 0, ma per test va bene)
  alarmState: 'IDLE'       // 'IDLE' | 'IMPACT' | 'THEFT'
};

let impactTimer = null;    // timer per il timeout dell'impact (20 secondi)

const DEVICE_ID = 'ESP_ACT';

// Helper per resettare il timer impact
function clearImpactTimer() {
  if (impactTimer) {
    clearTimeout(impactTimer);
    impactTimer = null;
  }
}

// Funzione per riportare a IDLE dopo 20 secondi (solo se è ancora in IMPACT)
function startImpactTimer() {
  clearImpactTimer();
  impactTimer = setTimeout(() => {
    if (state.alarmState === 'IMPACT') {
      console.log('[TIMER] Impact timeout -> IDLE');
      state.alarmState = 'IDLE';
    }
    impactTimer = null;
  }, 20000); // 20 secondi come nel firmware
}

// -------------------- ROUTE HANDLER --------------------

// GET /
app.get('/', (req, res) => {
  res.json({
    id: DEVICE_ID,
    status: 'ok',
    message: 'ESP32 Web Server is running (mock)'
  });
});

// GET /state
app.get('/state', (req, res) => {
  res.json({
    id: DEVICE_ID,
    brightness: state.brightness,
    alarmState: state.alarmState
  });
});

// POST /ambientlight
app.post('/ambientlight', (req, res) => {
  const { brightness } = req.body;
  if (brightness === undefined || typeof brightness !== 'number') {
    return res.status(400).json({ error: 'Invalid JSON, expected {"brightness": 0-100}' });
  }
  let val = Math.min(100, Math.max(0, Math.round(brightness)));
  state.brightness = val;
  console.log(`[AMBIENT] Brightness set to ${val}%`);
  res.json({
    id: DEVICE_ID,
    status: 'ok',
    message: 'Brightness updated'
  });
});

// POST /impact
app.post('/impact', (req, res) => {
  console.log('[IMPACT] Triggered');
  // Se non siamo in THEFT, passiamo a IMPACT e avviamo il timer
  if (state.alarmState !== 'THEFT') {
    state.alarmState = 'IMPACT';
    startImpactTimer();
  } else {
    // se siamo in THEFT, l'impact viene ignorato (come nel firmware)
    console.log('[IMPACT] Ignored because THEFT is active');
  }
  res.json({
    id: DEVICE_ID,
    status: 'ok',
    message: 'Impact alarm triggered'
  });
});

// POST /theft
app.post('/theft', (req, res) => {
  console.log('[THEFT] Triggered');
  state.alarmState = 'THEFT';
  clearImpactTimer(); // il timer impact viene fermato
  res.json({
    id: DEVICE_ID,
    status: 'ok',
    message: 'Theft alarm triggered'
  });
});

// POST /reset
app.post('/reset', (req, res) => {
  console.log('[RESET] Triggered');
  state.alarmState = 'IDLE';
  clearImpactTimer();
  res.json({
    id: DEVICE_ID,
    status: 'ok',
    message: 'Alarms reset'
  });
});

// Avvia il server
app.listen(PORT, () => {
  console.log(`ESP-ACT-MOCK running on port ${PORT}`);
  console.log(`State: brightness=${state.brightness}, alarm=${state.alarmState}`);
});
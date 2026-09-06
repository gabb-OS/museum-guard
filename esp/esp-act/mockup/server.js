// esp-act-mock/server.js
const express = require('express');
const app = express();
const PORT = process.env.HTTP_PORT || 80;

app.use(express.json());

// Internal state mimicking the firmware
let state = {
  brightness: 30,          // Starts at 0 in actual firmware, but 30 is fine for testing
  alarmState: 'IDLE'       // 'IDLE' | 'IMPACT' | 'THEFT'
};

let impactTimer = null;    // Tracks the 20-second impact timeout

const DEVICE_ID = 'ESP_ACT';

// Clear the impact timeout timer
function clearImpactTimer() {
  if (impactTimer) {
    clearTimeout(impactTimer);
    impactTimer = null;
  }
}

// Revert to IDLE after 20s, but only if still in IMPACT
function startImpactTimer() {
  clearImpactTimer();
  impactTimer = setTimeout(() => {
    if (state.alarmState === 'IMPACT') {
      console.log('[TIMER] Impact timeout -> IDLE');
      state.alarmState = 'IDLE';
    }
    impactTimer = null;
  }, 20000); // 20 seconds, matching firmware behavior
}

// -------------------- ROUTE HANDLERS --------------------

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
  // Switch to IMPACT and start timer, unless already in THEFT
  if (state.alarmState !== 'THEFT') {
    state.alarmState = 'IMPACT';
    startImpactTimer();
  } else {
    // Ignore impact if THEFT is already active (matches firmware)
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
  clearImpactTimer(); // Stop the impact timer
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

// Start the server
app.listen(PORT, () => {
  console.log(`ESP-ACT-MOCK running on port ${PORT}`);
  console.log(`State: brightness=${state.brightness}, alarm=${state.alarmState}`);
});
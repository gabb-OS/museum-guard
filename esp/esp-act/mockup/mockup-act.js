const ESP32_IP = "10.78.189.198"; // Ensure this matches your ESP32's IP
const BASE_URL = `http://${ESP32_IP}`;

// Helper
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function sendImpact() {
  console.log("Triggering Impact...");
  const res = await fetch(`${BASE_URL}/impact`, { method: "POST" });
  console.log(res.status, await res.text());
}

async function sendTheft() {
  console.log("Triggering Theft...");
  const res = await fetch(`${BASE_URL}/theft`, { method: "POST" });
  console.log(res.status, await res.text());
}

async function sendReset() {
  console.log("Triggering Reset...");
  const res = await fetch(`${BASE_URL}/reset`, { method: "POST" });
  console.log(res.status, await res.text());
}

async function sendBrightness(value) {
  console.log(`Setting Brightness to ${value}%...`);
  const res = await fetch(`${BASE_URL}/ambientlight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ "brightness": value }),
  });
  console.log(res.status, await res.text());
}

// Main
(async () => {
  console.log("! STARTING ESP32 TEST SEQUENCE \n");

  console.log("[1] Testing Ambient Light Fading...");
  for (const level of [0, 25, 50, 75, 100, 0]) {
    await sendBrightness(level);
    await delay(1000);
  }

  console.log("\n[1.1] Testing Impact Alarm wrong payload...");
  await sendBrightness("wrongpayload");

  console.log("\n[2] Testing Impact Alarm...");
  await sendImpact();
  console.log("Blinking LED for 5 seconds...");
  await delay(5000);

  console.log("\n[3] Testing Theft Override...");
  await sendTheft();
  console.log("Solid red. Waiting 3 seconds...");
  await delay(3000);

  console.log("\n[4] Sending Impact while Theft is active...");
  await sendImpact();
  console.log("Solid red. Waiting 3 seconds...");
  await delay(3000);

  console.log("\n[5] Testing Reset...");
  await sendReset();
  console.log("All off");

  console.log("\n--- TEST SEQUENCE COMPLETE ---");
})();
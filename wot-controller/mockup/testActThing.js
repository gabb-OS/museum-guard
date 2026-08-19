// test-pipeline.js

const BASE_URL = "http://localhost:8080/actuator"

const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function runTests() {
    console.log("Starting automated WoT pipeline tests...\n");

    try {
        console.log("TEST 1: Reading alarm state...");
        let resState = await fetch(`${BASE_URL}/properties/alarmLightState`);
        if (!resState.ok) throw new Error(`HTTP Error: ${resState.status}`);
        let stateData = await resState.json();
        console.log("SUCCESS: Current state ->", stateData);


        console.log("\nTEST 2: Setting brightness to 80...");
        const resBrightness = await fetch(`${BASE_URL}/actions/regulateBrightness`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(80) 
        });
        if (!resBrightness.ok) throw new Error(`HTTP Error: ${resBrightness.status}`);
        console.log("SUCCESS: Brightness updated!");


        console.log("\nTEST 3: Simulating impact (startBlink)...");
        const resImpact = await fetch(`${BASE_URL}/actions/triggerImpactBlink`, {
            method: "POST"
        });
        if (!resImpact.ok) throw new Error(`HTTP Error: ${resImpact.status}`);
        console.log("SUCCESS: Impact alarm activated!");

        resState = await fetch(`${BASE_URL}/properties/alarmLightState`);
        if (!resState.ok) throw new Error(`HTTP Error: ${resState.status}`);
        stateData = await resState.json();
        console.log("SUCCESS: Current state ->", stateData);
        await delay(4000);

        
        console.log("\nTEST 4: Simulating theft...");
        const resTheft = await fetch(`${BASE_URL}/actions/triggerTheftAlarm`, {
            method: "POST"
        });
        if (!resTheft.ok) throw new Error(`HTTP Error: ${resTheft.status}`);
        console.log("SUCCESS: Theft alarm activated!");

        resState = await fetch(`${BASE_URL}/properties/alarmLightState`);
        if (!resState.ok) throw new Error(`HTTP Error: ${resState.status}`);
        stateData = await resState.json();
        console.log("SUCCESS: Current state ->", stateData);
        await delay(4000);
        
        console.log("\nTEST 5: Resetting alarms...");
        const resReset = await fetch(`${BASE_URL}/actions/resetAlarmLight`, {
            method: "POST"
        });
        if (!resReset.ok) throw new Error(`HTTP Error: ${resReset.status}`);
        console.log("SUCCESS: Alarms reset!");

        console.log("\nALL TESTS PASSED SUCCESSFULLY!");

    } catch (err) {
        console.error("\nTEST FAILED:", err.message);
        console.error("Make sure the Node.js server is running and the URL is correct.");
    }
}

runTests();
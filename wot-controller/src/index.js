import corePkg from "@node-wot/core";
import httpPkg from "@node-wot/binding-http";

const { Servient } = corePkg;
const { HttpServer } = httpPkg;

import { createEspSenTD } from "./things/espSenThing.js";
import { createEspActTD } from "./things/espActThing.js";

async function main() {
    const servient = new Servient();
    servient.addServer(new HttpServer({ port: 8080 }));

    const WoT = await servient.start();
    console.log("Servient WoT started");

    // Produce the two Things: reachable via HTTP at http://node-wot:8080/sensor and /actuator
    const espSenThing = await createEspSenTD(WoT);
    const espActThing = await createEspActTD(WoT);

    console.log("TD ESP-SEN:", espSenThing.getThingDescription().id);
    console.log("TD ESP-ACT:", espActThing.getThingDescription().id);
}

main().catch(err => {
    console.error("Fatal error in Controller:", err);
    process.exit(1);
});
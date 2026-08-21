const { Servient } = require("@node-wot/core");
const { HttpClientFactory } = require("@node-wot/binding-http");
const { CoapClientFactory } = require("@node-wot/binding-coap");
const { WsClientFactory } = require("@node-wot/binding-ws");
const { FileClientFactory } = require("@node-wot/binding-file");

async function main() {
    // Create the Servient in Client mode
    // get the actuator and sensor Thing Descriptions from the WoT Controller
    const { sensor, actuator } = await initWotConsumer();
    
    // Init InfluxDB

    // TODO all inits
    
}

main().catch(err => {
    console.error("Fatal error in Mash-up app:", err);
    process.exit(1);
});

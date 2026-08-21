const { Servient } = require("@node-wot/core");
const { HttpClientFactory } = require("@node-wot/binding-http");
const { CoapClientFactory } = require("@node-wot/binding-coap");
const { WsClientFactory } = require("@node-wot/binding-ws");
const { FileClientFactory } = require("@node-wot/binding-file");

async function main() {
	const servient = new Servient();  
    servient.addClientFactory(new HttpClientFactory());
    const wot = await servient.start();

    //Init actuator and sensor Thing from the WoT Controller
    const { sensor, actuator } = await initWotConsumer(wot);
    
    // Init InfluxDB

    // TODO all inits
    
}

main().catch(err => {
    console.error("Fatal error in Mash-up app:", err);
    process.exit(1);
});

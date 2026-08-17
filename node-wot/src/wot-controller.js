/*---------- EXPOSE the Things ----------*/
// Required steps to create a servient for creating a thing
const { Servient } = require("@node-wot/core");
const { HttpServer } = require("@node-wot/binding-http");
const { espActTD } = require('./espActTD.js');

const servient = new Servient();
servient.addServer(new HttpServer());

servient.start().then( async (WoT) => {

    console.log("Servient started. Producing things...");
    
    // Call the imported functions and pass the WoT object
    await createEspActTD(WoT);
    // await createEspSen(WoT);
   
}).catch(console.error);;
import coap from "coap";

export class CoapClient {
  constructor(host, port = 5683) {
    this.host = host;
    this.port = port;
  }

  get(path) {
    return new Promise((resolve, reject) => {
      const req = coap.request({ host: this.host, port: this.port, method: "GET", pathname: path });
      req.on("response", (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      });
      req.on("error", reject);
      req.end();
    });
  }

  put(path, payload = "") {
    return new Promise((resolve, reject) => {
      const req = coap.request({ host: this.host, port: this.port, method: "PUT", pathname: path });
      req.on("response", (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => resolve({ code: res.code, body: Buffer.concat(chunks).toString("utf8") }));
      });
      req.on("error", reject);
      req.end(payload);
    });
  }

  observe(path, onData, onError) {
    let active = true;
    const start = () => {
      const req = coap.request({ host: this.host, port: this.port, method: "GET", observe: true, pathname: path });
      req.on("response", (res) => {
        res.on("data", (chunk) => {
          if (!active) return;
          const text = chunk.toString("utf8");
          if (text.length > 0) onData(text);
        });
        res.on("error", (err) => { if (onError) onError(err); scheduleRetry(); });
      });
      req.on("error", (err) => { if (onError) onError(err); scheduleRetry(); });
      req.end();
    };
    const scheduleRetry = () => { if (active) setTimeout(start, 3000); };
    start();
    return () => { active = false; };
  }
}
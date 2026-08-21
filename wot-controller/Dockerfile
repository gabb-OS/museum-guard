# node-wot/Dockerfile
FROM node:18-alpine

WORKDIR /app

# Copia i file delle dipendenze e installa (produzione)
COPY package*.json ./
RUN npm install --production

# Copia tutto il resto del codice sorgente
COPY . .

# Esponi la porta su cui il serviente WoT ascolta (8080 come da index.js)
EXPOSE 8080

# Avvia il controller
CMD ["node", "src/index.js"]
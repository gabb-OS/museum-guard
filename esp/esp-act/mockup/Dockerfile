# esp-act-mock/Dockerfile
FROM node:18-alpine

WORKDIR /app

# Copia package.json (se non hai dipendenze, creiamo un file minimo)
COPY package*.json ./
RUN npm install --production

# Copia il sorgente
COPY server.js .

EXPOSE 80

CMD ["node", "server.js"]
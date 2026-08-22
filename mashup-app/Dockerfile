FROM node:20-alpine

WORKDIR /app

# Copiamo solo i manifest prima per sfruttare la cache dei layer Docker
COPY package*.json ./
RUN npm install --omit=dev

# Copiamo il resto del codice sorgente
COPY . .

CMD ["node", "src/index.js"]
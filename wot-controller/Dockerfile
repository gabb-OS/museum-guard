# node-wot/Dockerfile
FROM node:18-alpine

WORKDIR /app

# Copy dependency files and install (production)
COPY package*.json ./
RUN npm install --production

# Copy all the rest of the source code
COPY . .

# Expose the port the WoT server listens on (8080 as per index.js)
EXPOSE 8080

# Avvia il controller
CMD ["node", "src/index.js"]
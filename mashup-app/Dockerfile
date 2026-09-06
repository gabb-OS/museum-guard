FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

# Let's copy the source code
COPY . .

CMD ["node", "src/index.js"]
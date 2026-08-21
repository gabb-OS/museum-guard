FROM python:3.11-alpine

WORKDIR /app

# Installa strumenti di compilazione e autotools
RUN apk add --no-cache build-base autoconf automake libtool

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY esp_sen_mock.py .

EXPOSE 5683/udp

CMD ["python", "esp_sen_mock.py"]
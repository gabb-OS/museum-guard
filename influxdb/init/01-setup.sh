#!/bin/bash
# influxdb/init/01-setup.sh
#
# Eseguito automaticamente da influxdb:2.7 al PRIMO avvio (quando il
# volume dati e' vuoto), DOPO il setup base fatto dagli env
# DOCKER_INFLUXDB_INIT_* (utente/org/bucket/token gia' creati a quel
# punto). Vedi docker-compose.yml: ./influxdb/init e' montato su
# /docker-entrypoint-initdb.d.
#
# InfluxDB 2.x e' schemaless: measurement/tag/field vengono creati al
# volo al primo punto scritto in line protocol (lo fa il mash-up via
# influxService.js). Qui impostiamo solo cose che il setup automatico
# NON fa da solo, cioe' la retention del bucket.

set -e

BUCKET_NAME="${DOCKER_INFLUXDB_INIT_BUCKET}"
RETENTION="${INFLUXDB_RETENTION:-30d}"

echo "[influx-init] Cerco bucket '$BUCKET_NAME'..."
BUCKET_ID=$(influx bucket list --name "$BUCKET_NAME" --hide-headers | awk '{print $1}')

if [ -n "$BUCKET_ID" ]; then
    influx bucket update --id "$BUCKET_ID" --retention "$RETENTION"
    echo "[influx-init] Retention di '$BUCKET_NAME' impostata a $RETENTION"
else
    echo "[influx-init] ATTENZIONE: bucket '$BUCKET_NAME' non trovato, retention non modificata"
fi

echo "[influx-init] Setup completato."
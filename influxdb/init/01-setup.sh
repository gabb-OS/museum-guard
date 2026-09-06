#!/bin/bash
# influxdb/init/01-setup.sh
#
# Automatically run by influxdb:2.7 on first startup.
# Here we only set things that the automatic setup
# does NOT do by itself, namely bucket retention.

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
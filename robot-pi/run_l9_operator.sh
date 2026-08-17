#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <expected-delivery-uuid>" >&2
  exit 2
fi

expected_delivery_id="$1"
if [[ ! "$expected_delivery_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]; then
  echo "The expected delivery ID must be a lowercase UUID." >&2
  exit 2
fi

broker_host_file="${HOME}/.config/miit-l9/broker.host"
if [[ -r "$broker_host_file" ]]; then
  IFS= read -r L9_MQTT_HOST <"$broker_host_file"
else
  read -r -s -p "EMQX MQTT host: " L9_MQTT_HOST
  printf '\n'
fi

if [[ -z "$L9_MQTT_HOST" ]]; then
  unset L9_MQTT_HOST
  echo "The MQTT hostname is required." >&2
  exit 2
fi
if [[ ! "$L9_MQTT_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] \
  || [[ "$L9_MQTT_HOST" != *.* ]]; then
  unset L9_MQTT_HOST
  echo "Enter the hostname only: no scheme, key name, port, quotes, or spaces." >&2
  exit 2
fi
if ! getent ahosts "$L9_MQTT_HOST" >/dev/null 2>&1; then
  unset L9_MQTT_HOST
  echo "The Pi could not resolve that hostname. Check it and try again." >&2
  exit 2
fi

read -r -s -p "robot-test-l6 MQTT password: " L9_MQTT_PASSWORD
printf '\n'
if [[ -z "$L9_MQTT_PASSWORD" ]]; then
  unset L9_MQTT_HOST L9_MQTT_PASSWORD
  echo "The MQTT password is required." >&2
  exit 2
fi

export L9_MQTT_HOST
export L9_MQTT_PASSWORD
export L9_MQTT_USERNAME="robot-test-l6"
export L9_MQTT_CLIENT_ID="robot-test-l6-subscriber"

cleanup() {
  unset L9_MQTT_HOST L9_MQTT_PASSWORD
  unset L9_MQTT_USERNAME L9_MQTT_CLIENT_ID
}
trap cleanup EXIT

exec_python="/opt/miit-rover/source/robot-pi/.venv/bin/python"
exec "$exec_python" \
  "/home/evdelivery/miit-l9/l9_workflow_simulator.py" \
  --expected-delivery-id "$expected_delivery_id" \
  --duration 900

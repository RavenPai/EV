#!/usr/bin/env bash

# Install the display-only Raspberry Pi service without copying or replacing
# MQTT credentials. Run this script on the Pi from a checked-out/staged EV
# repository:
#
#   sudo bash robot-pi/install_delivery_display.sh

set -Eeuo pipefail

readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TARGET_DIR="/opt/miit-rover/source/robot-pi"
readonly ENV_FILE="/etc/miit-rover/robot.env"
readonly UNIT_NAME="miit-delivery-display.service"
readonly UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
readonly OLD_UNIT_NAME="miit-rover-agent.service"
readonly PYTHON_BIN="${TARGET_DIR}/.venv/bin/python"

STAGE_DIR=""
BACKUP_DIR=""
ROLLBACK_ARMED=0
OLD_UNIT_ACTIVE="unknown"
OLD_UNIT_ENABLED="unknown"
DISPLAY_UNIT_ACTIVE="unknown"
DISPLAY_UNIT_ENABLED="unknown"

log() {
  printf '[delivery-display-install] %s\n' "$*"
}

fail() {
  printf '[delivery-display-install] ERROR: %s\n' "$*" >&2
  return 1
}

unit_state() {
  local operation="$1"
  local unit="$2"
  systemctl "$operation" "$unit" 2>/dev/null || true
}

restore_unit_state() {
  local unit="$1"
  local enabled_state="$2"
  local active_state="$3"

  systemctl disable --now "$unit" >/dev/null 2>&1 || true
  if [[ "$enabled_state" == "enabled" ]]; then
    systemctl enable "$unit" >/dev/null 2>&1 || true
  fi
  if [[ "$active_state" == "active" ]]; then
    systemctl start "$unit" >/dev/null 2>&1 || true
  fi
}

backup_target() {
  local target="$1"
  local label="$2"
  if [[ -e "$target" || -L "$target" ]]; then
    cp -a -- "$target" "${BACKUP_DIR}/${label}"
  else
    : >"${BACKUP_DIR}/${label}.absent"
  fi
}

restore_target() {
  local target="$1"
  local label="$2"
  if [[ -e "${BACKUP_DIR}/${label}" || -L "${BACKUP_DIR}/${label}" ]]; then
    cp -a -- "${BACKUP_DIR}/${label}" "$target"
  elif [[ -e "${BACKUP_DIR}/${label}.absent" ]]; then
    rm -f -- "$target"
  fi
}

rollback() {
  local exit_code="$1"
  if [[ "$ROLLBACK_ARMED" -ne 1 ]]; then
    exit "$exit_code"
  fi

  trap - ERR
  printf '[delivery-display-install] Installation failed; restoring the previous deployment.\n' >&2
  systemctl disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
  restore_target "${TARGET_DIR}/delivery_display.py" "delivery_display.py"
  restore_target "${TARGET_DIR}/message_contract.py" "message_contract.py"
  restore_target "$UNIT_PATH" "$UNIT_NAME"
  systemctl daemon-reload >/dev/null 2>&1 || true
  restore_unit_state "$UNIT_NAME" "$DISPLAY_UNIT_ENABLED" "$DISPLAY_UNIT_ACTIVE"
  restore_unit_state "$OLD_UNIT_NAME" "$OLD_UNIT_ENABLED" "$OLD_UNIT_ACTIVE"
  printf '[delivery-display-install] Previous files and service states restored. Backup retained at %s\n' "$BACKUP_DIR" >&2
  exit "$exit_code"
}

cleanup() {
  case "$STAGE_DIR" in
    /opt/miit-rover/.delivery-display-stage.*)
      [[ ! -d "$STAGE_DIR" ]] || rm -rf -- "$STAGE_DIR"
      ;;
  esac
}

trap 'rollback "$?"' ERR
trap cleanup EXIT

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '%s\n' \
    'Usage: sudo bash robot-pi/install_delivery_display.sh' \
    'Installs the display-only service while preserving /etc/miit-rover/robot.env.'
  exit 0
fi
[[ "$#" -eq 0 ]] || fail "unknown argument: $1"
[[ "${EUID}" -eq 0 ]] || fail "run with sudo: sudo bash robot-pi/install_delivery_display.sh"

for source_file in delivery_display.py message_contract.py miit-delivery-display.service; do
  [[ -f "${SCRIPT_DIR}/${source_file}" ]] || fail "missing source file: ${SCRIPT_DIR}/${source_file}"
done
[[ -f "$ENV_FILE" && -s "$ENV_FILE" ]] || fail "existing MQTT environment file is missing or empty: $ENV_FILE"
[[ -x "$PYTHON_BIN" ]] || fail "existing rover virtual environment is missing: $PYTHON_BIN"

# Check names only. Do not source, print, copy, or rewrite the credential file.
for required_name in ROBOT_ID MQTT_HOST MQTT_PASSWORD MQTT_CA_FILE; do
  grep -Eq "^[[:space:]]*${required_name}=" "$ENV_FILE" \
    || fail "${required_name} is missing from the existing environment file"
done

install -d -o root -g root -m 0750 /opt/miit-rover
install -d -o root -g root -m 0750 /opt/miit-rover/backups
install -d -o root -g root -m 0755 "$TARGET_DIR"
install -d -o rover -g rover -m 0750 /var/lib/miit-rover

STAGE_DIR="$(mktemp -d /opt/miit-rover/.delivery-display-stage.XXXXXX)"
install -o root -g root -m 0644 "${SCRIPT_DIR}/delivery_display.py" "${STAGE_DIR}/delivery_display.py"
install -o root -g root -m 0644 "${SCRIPT_DIR}/message_contract.py" "${STAGE_DIR}/message_contract.py"
install -o root -g root -m 0644 "${SCRIPT_DIR}/miit-delivery-display.service" "${STAGE_DIR}/${UNIT_NAME}"

log "validating staged Python without loading MQTT credentials"
PYTHONPATH="$STAGE_DIR" PYTHONPYCACHEPREFIX="${STAGE_DIR}/pycache" \
  "$PYTHON_BIN" -m py_compile \
  "${STAGE_DIR}/delivery_display.py" "${STAGE_DIR}/message_contract.py"
PYTHONPATH="$STAGE_DIR" "$PYTHON_BIN" -c \
  'import paho.mqtt.client; import delivery_display; import message_contract'

OLD_UNIT_ACTIVE="$(unit_state is-active "$OLD_UNIT_NAME")"
OLD_UNIT_ENABLED="$(unit_state is-enabled "$OLD_UNIT_NAME")"
DISPLAY_UNIT_ACTIVE="$(unit_state is-active "$UNIT_NAME")"
DISPLAY_UNIT_ENABLED="$(unit_state is-enabled "$UNIT_NAME")"

readonly INSTALL_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/opt/miit-rover/backups/delivery-display-${INSTALL_STAMP}"
install -d -o root -g root -m 0700 "$BACKUP_DIR"
backup_target "${TARGET_DIR}/delivery_display.py" "delivery_display.py"
backup_target "${TARGET_DIR}/message_contract.py" "message_contract.py"
backup_target "$UNIT_PATH" "$UNIT_NAME"
ROLLBACK_ARMED=1

log "installing root-owned display files; existing MQTT credentials remain unchanged"
systemctl stop "$UNIT_NAME" >/dev/null 2>&1 || true
install -o root -g root -m 0644 "${STAGE_DIR}/delivery_display.py" "${TARGET_DIR}/delivery_display.py"
install -o root -g root -m 0644 "${STAGE_DIR}/message_contract.py" "${TARGET_DIR}/message_contract.py"
install -o root -g root -m 0644 "${STAGE_DIR}/${UNIT_NAME}" "$UNIT_PATH"

systemctl daemon-reload
systemd-analyze verify "$UNIT_PATH"

# Both services use the robot's one MQTT client identity. Running both would
# cause broker disconnect loops and duplicate command consumers.
log "switching from the rover bridge to the display-only service"
systemctl disable --now "$OLD_UNIT_NAME"
systemctl enable --now "$UNIT_NAME"

DISPLAY_PORT="8080"
PORT_LINE="$(grep -E '^[[:space:]]*DELIVERY_DISPLAY_PORT=' "$ENV_FILE" | tail -n 1 || true)"
if [[ -n "$PORT_LINE" ]]; then
  DISPLAY_PORT="${PORT_LINE#*=}"
  DISPLAY_PORT="${DISPLAY_PORT%%#*}"
  DISPLAY_PORT="${DISPLAY_PORT//\"/}"
  DISPLAY_PORT="${DISPLAY_PORT//\'/}"
  DISPLAY_PORT="${DISPLAY_PORT#"${DISPLAY_PORT%%[![:space:]]*}"}"
  DISPLAY_PORT="${DISPLAY_PORT%"${DISPLAY_PORT##*[![:space:]]}"}"
fi
[[ "$DISPLAY_PORT" =~ ^[0-9]+$ ]] \
  && (( DISPLAY_PORT >= 1 && DISPLAY_PORT <= 65535 )) \
  || fail "DELIVERY_DISPLAY_PORT must be an integer from 1 to 65535"

log "waiting for HTTP health and the EMQX command subscription"
READY=0
for _attempt in $(seq 1 20); do
  if systemctl is-active --quiet "$UNIT_NAME"; then
    if "$PYTHON_BIN" - "$DISPLAY_PORT" <<'PY' 2>/dev/null
import json
import sys
from urllib.request import urlopen

with urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=1) as response:
    health = json.load(response)
if not health.get("ok") or not health.get("mqttConnected") or not health.get("mqttSubscribed"):
    raise SystemExit(1)
PY
    then
      READY=1
      break
    fi
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  journalctl -u "$UNIT_NAME" -n 25 --no-pager >&2 || true
  fail "display service did not become HTTP/MQTT ready"
fi

ROLLBACK_ARMED=0
log "installation complete"
log "service: ${UNIT_NAME} (active and enabled)"
log "local display: http://127.0.0.1:${DISPLAY_PORT}/"
log "rollback backup retained at: ${BACKUP_DIR}"

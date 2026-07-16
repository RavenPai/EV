"""Minimal MQTT command bridge for the Raspberry Pi mission computer.

This bridge validates cloud command envelopes, persists processed command IDs,
forwards only safety/mission-level messages, and publishes acknowledgements.
Navigation remains a separate local module (marker route or ROS 2/Nav2).
"""

from __future__ import annotations

import json
import os
import sqlite3
import ssl
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import serial


ROBOT_ID = os.environ.get("ROBOT_ID", "robot-01")
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
SERIAL_PORT = os.environ.get("ESP32_SERIAL_PORT", "/dev/ttyUSB0")
STATE_DIR = Path(os.environ.get("ROBOT_STATE_DIR", "/var/lib/miit-rover"))
STATE_DIR.mkdir(parents=True, exist_ok=True)

COMMAND_TOPIC = f"miit/robots/{ROBOT_ID}/commands"
ACK_TOPIC = f"miit/robots/{ROBOT_ID}/acks"
STATE_TOPIC = f"miit/robots/{ROBOT_ID}/state"
PRESENCE_TOPIC = f"miit/robots/{ROBOT_ID}/presence"

db = sqlite3.connect(STATE_DIR / "commands.db", check_same_thread=False)
db.execute("create table if not exists processed_commands (id text primary key, processed_at text not null)")
db.commit()
esp32 = serial.Serial(SERIAL_PORT, 115200, timeout=1)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def acknowledge(client: mqtt.Client, command_id: str, status: str, reason: str = "") -> None:
    client.publish(ACK_TOPIC, json.dumps({
        "schemaVersion": 1,
        "commandId": command_id,
        "robotId": ROBOT_ID,
        "status": status,
        "reason": reason,
        "at": utc_now(),
    }), qos=1, retain=False)


def already_processed(command_id: str) -> bool:
    return db.execute("select 1 from processed_commands where id = ?", (command_id,)).fetchone() is not None


def mark_processed(command_id: str) -> None:
    db.execute("insert or ignore into processed_commands values (?, ?)", (command_id, utc_now()))
    db.commit()


def send_to_esp32(command: str) -> None:
    # ESP32 must still enforce its own heartbeat, state, E-stop and CRC policy.
    frame = {"v": 1, "cmd": command, "ttlMs": 300 if command == "STOP" else 2000}
    esp32.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
    esp32.flush()


def on_connect(client: mqtt.Client, _userdata, _flags, reason_code, _properties) -> None:
    if reason_code != 0:
        raise RuntimeError(f"MQTT connection failed: {reason_code}")
    client.subscribe(COMMAND_TOPIC, qos=1)
    client.publish(PRESENCE_TOPIC, json.dumps({"online": True, "at": utc_now()}), qos=1, retain=True)


def on_message(client: mqtt.Client, _userdata, message: mqtt.MQTTMessage) -> None:
    try:
        envelope = json.loads(message.payload)
        command_id = str(envelope["commandId"])
        if envelope.get("schemaVersion") != 1 or envelope.get("robotId") != ROBOT_ID:
            acknowledge(client, command_id, "REJECTED", "wrong schema or robot")
            return
        if datetime.fromisoformat(envelope["expiresAt"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            acknowledge(client, command_id, "REJECTED", "expired")
            return
        if already_processed(command_id):
            acknowledge(client, command_id, "ACKNOWLEDGED", "duplicate; previous result retained")
            return

        command = envelope["command"]
        if command in {"ESTOP", "PAUSE"}:
            send_to_esp32("STOP")
        elif command == "RESUME":
            # Mission manager may resume only after local sensor/state checks pass.
            pass
        elif command == "RETURN_HOME":
            (STATE_DIR / "mission_request.json").write_text(json.dumps({"type": "RETURN_HOME", "commandId": command_id}))
        elif command == "START_MISSION":
            (STATE_DIR / "mission_request.json").write_text(json.dumps({"type": "START_MISSION", **envelope["payload"], "commandId": command_id}))
        else:
            acknowledge(client, command_id, "REJECTED", "unsupported command")
            return

        mark_processed(command_id)
        acknowledge(client, command_id, "ACKNOWLEDGED")
    except Exception as exc:  # A malformed command must never move the robot.
        send_to_esp32("STOP")
        client.publish(STATE_TOPIC, json.dumps({"mode": "FAULT", "reason": str(exc), "at": utc_now()}), qos=1)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{ROBOT_ID}-pi")
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
client.will_set(PRESENCE_TOPIC, json.dumps({"online": False}), qos=1, retain=True)
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_forever()

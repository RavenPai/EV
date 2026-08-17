"""Raspberry Pi delivery display with operator-controlled MQTT acknowledgement.

This process intentionally performs no navigation, serial communication, or
motor control. It receives delivery snapshots on the existing robot command
topic, persists them in SQLite, serves a small local web page, and publishes a
single application acknowledgement only after the operator presses the button.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import signal
import sqlite3
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt

from message_contract import (
    prepare_ack_payload,
    prepare_command_envelope,
    validate_command_transport,
    validate_robot_id,
)


LOGGER = logging.getLogger("miit-delivery-display")
ACK_PATH = re.compile(
    r"^/api/deliveries/([0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})/ack$",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass(frozen=True)
class DisplayConfig:
    robot_id: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_ca_file: str
    mqtt_client_id: str
    bind_host: str
    http_port: int
    database_path: Path
    version: str
    presence_interval_seconds: float

    @classmethod
    def from_environment(cls) -> "DisplayConfig":
        robot_id = validate_robot_id(os.environ.get("ROBOT_ID", "robot-01"))
        mqtt_username = os.environ.get("MQTT_USERNAME", robot_id)
        if mqtt_username != robot_id:
            raise RuntimeError(
                "MQTT_USERNAME must equal ROBOT_ID for webhook identity validation"
            )

        mqtt_host = os.environ.get("MQTT_HOST", "").strip()
        mqtt_password = os.environ.get("MQTT_PASSWORD", "")
        mqtt_ca_file = os.environ.get("MQTT_CA_FILE", "").strip()
        if not mqtt_host or not mqtt_password or not mqtt_ca_file:
            raise RuntimeError("MQTT_HOST, MQTT_PASSWORD, and MQTT_CA_FILE are required")

        state_dir = Path(os.environ.get("ROBOT_STATE_DIR", "/var/lib/miit-rover"))
        return cls(
            robot_id=robot_id,
            mqtt_host=mqtt_host,
            mqtt_port=int(os.environ.get("MQTT_PORT", "8883")),
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            mqtt_ca_file=mqtt_ca_file,
            mqtt_client_id=os.environ.get("MQTT_CLIENT_ID", f"{robot_id}-pi"),
            bind_host=os.environ.get("DELIVERY_DISPLAY_HOST", "0.0.0.0"),
            http_port=int(os.environ.get("DELIVERY_DISPLAY_PORT", "8080")),
            database_path=Path(
                os.environ.get(
                    "DELIVERY_DISPLAY_DATABASE",
                    str(state_dir / "delivery-display.db"),
                )
            ),
            version=os.environ.get(
                "DELIVERY_DISPLAY_VERSION", "pi-delivery-display-1.0.0"
            ),
            presence_interval_seconds=float(
                os.environ.get("PRESENCE_INTERVAL_SECONDS", "15")
            ),
        )


class DeliveryStore:
    """Thread-safe durable storage for received display commands."""

    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            create table if not exists delivery_cards (
              command_id text primary key,
              delivery_id text not null,
              payload_hash text not null,
              envelope_json text not null,
              received_at text not null,
              expires_at text not null,
              acknowledged_at text,
              acknowledgement_json text
            )
            """
        )
        self._connection.commit()

    def save(self, envelope: dict[str, Any], received_at: str) -> bool:
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        command_id = envelope["commandId"]
        delivery_id = envelope["payload"]["deliveryId"]
        with self._lock:
            existing = self._connection.execute(
                "select payload_hash from delivery_cards where command_id = ?",
                (command_id,),
            ).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise ValueError("commandId was reused with different delivery data")
                return False
            self._connection.execute(
                """
                insert into delivery_cards (
                  command_id, delivery_id, payload_hash, envelope_json,
                  received_at, expires_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    delivery_id,
                    payload_hash,
                    encoded,
                    received_at,
                    envelope["expiresAt"],
                ),
            )
            self._connection.commit()
            return True

    def list_cards(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "select * from delivery_cards order by received_at desc"
            ).fetchall()
        return [self._row_to_card(row) for row in rows]

    def get_card(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "select * from delivery_cards where command_id = ?", (command_id,)
            ).fetchone()
        return self._row_to_card(row) if row else None

    def mark_acknowledged(
        self, command_id: str, acknowledgement: dict[str, Any]
    ) -> None:
        encoded = json.dumps(acknowledgement, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._connection.execute(
                """
                update delivery_cards
                set acknowledged_at = ?, acknowledgement_json = ?
                where command_id = ? and acknowledged_at is null
                """,
                (acknowledgement["at"], encoded, command_id),
            )
            self._connection.commit()

    @staticmethod
    def _row_to_card(row: sqlite3.Row) -> dict[str, Any]:
        envelope = json.loads(row["envelope_json"])
        acknowledgement = (
            json.loads(row["acknowledgement_json"])
            if row["acknowledgement_json"]
            else None
        )
        return {
            "commandId": row["command_id"],
            "deliveryId": row["delivery_id"],
            "envelope": envelope,
            "receivedAt": row["received_at"],
            "expiresAt": row["expires_at"],
            "acknowledgedAt": row["acknowledged_at"],
            "acknowledgement": acknowledgement,
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class DeliveryDisplayApplication:
    def __init__(
        self,
        config: DisplayConfig,
        store: DeliveryStore,
        mqtt_client: mqtt.Client | None = None,
    ):
        self.config = config
        self.store = store
        self.mqtt_client = mqtt_client
        self.connected = threading.Event()
        self.subscribed = threading.Event()
        self.stopping = threading.Event()
        self._acknowledgement_lock = threading.Lock()

    @property
    def command_topic(self) -> str:
        return f"miit/robots/{self.config.robot_id}/commands"

    @property
    def acknowledgement_topic(self) -> str:
        return f"miit/robots/{self.config.robot_id}/acks"

    @property
    def presence_topic(self) -> str:
        return f"miit/robots/{self.config.robot_id}/presence"

    def presence_payload(self, online: bool) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "robotId": self.config.robot_id,
            "online": online,
            "at": utc_now(),
            "firmwareVersion": self.config.version,
        }

    def receive_command(
        self,
        *,
        topic: str,
        qos: int,
        retain: bool,
        payload: bytes | str,
        now: datetime | None = None,
    ) -> tuple[str, bool]:
        validate_command_transport(
            topic=topic,
            expected_topic=self.command_topic,
            qos=qos,
            retain=retain,
        )
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        envelope = json.loads(text)
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        normalized, _issued_at, expires_at = prepare_command_envelope(
            envelope,
            robot_id=self.config.robot_id,
            now=reference,
        )
        if normalized["command"] != "START_MISSION":
            raise ValueError("the delivery display accepts only START_MISSION")
        if expires_at <= reference:
            raise ValueError("delivery command is already expired")
        inserted = self.store.save(normalized, reference.isoformat())
        LOGGER.info(
            "delivery_received command_id=%s delivery_id=%s duplicate=%s",
            normalized["commandId"],
            normalized["payload"]["deliveryId"],
            not inserted,
        )
        return normalized["commandId"], inserted

    def acknowledge_delivery(
        self, command_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        with self._acknowledgement_lock:
            card = self.store.get_card(command_id)
            if not card:
                raise KeyError("delivery command was not found")
            if card["acknowledgement"]:
                return card["acknowledgement"]

            reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            if parse_utc(card["expiresAt"]) <= reference:
                raise ValueError("delivery command expired before acknowledgement")
            if not self.mqtt_client or not self.mqtt_client.is_connected():
                raise RuntimeError("MQTT is disconnected; try again after it reconnects")

            acknowledgement = prepare_ack_payload(
                robot_id=self.config.robot_id,
                command_id=command_id,
                status="COMPLETED",
                reason="Delivery information acknowledged on Raspberry Pi display",
                now=reference,
            )
            publish_info = self.mqtt_client.publish(
                self.acknowledgement_topic,
                json.dumps(acknowledgement, separators=(",", ":")),
                qos=1,
                retain=False,
            )
            if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT publish failed with result {publish_info.rc}")
            publish_info.wait_for_publish(timeout=10)
            if hasattr(publish_info, "is_published") and not publish_info.is_published():
                raise TimeoutError("MQTT broker did not confirm the acknowledgement")

            self.store.mark_acknowledged(command_id, acknowledgement)
            LOGGER.info("delivery_acknowledged command_id=%s", command_id)
            return acknowledgement

    def public_cards(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for card in self.store.list_cards():
            payload = card["envelope"]["payload"]
            delivery = payload.get("delivery", {})
            acknowledged = card["acknowledgement"] is not None
            result.append(
                {
                    "commandId": card["commandId"],
                    "deliveryId": card["deliveryId"],
                    "trackingCode": delivery.get("trackingCode", card["deliveryId"]),
                    "requesterName": delivery.get("requesterName", ""),
                    "requesterEmail": delivery.get("requesterEmail", ""),
                    "recipientName": delivery.get("recipientName", ""),
                    "recipientPhone": delivery.get("recipientPhone", ""),
                    "sourceName": delivery.get(
                        "sourceName", payload["sourceLocationId"]
                    ),
                    "destinationName": delivery.get(
                        "destinationName", payload["destinationLocationId"]
                    ),
                    "itemName": delivery.get("itemName", "Delivery"),
                    "category": delivery.get("category", ""),
                    "weightKg": delivery.get("weightKg"),
                    "priority": delivery.get("priority", "NORMAL"),
                    "notes": delivery.get("notes", ""),
                    "receivedAt": card["receivedAt"],
                    "expiresAt": card["expiresAt"],
                    "acknowledgedAt": card["acknowledgedAt"],
                    "acknowledged": acknowledged,
                    "expired": not acknowledged and parse_utc(card["expiresAt"]) <= now,
                }
            )
        return result

    def publish_presence(self, online: bool) -> None:
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return
        self.mqtt_client.publish(
            self.presence_topic,
            json.dumps(self.presence_payload(online), separators=(",", ":")),
            qos=1,
            retain=True,
        )


def render_page(application: DeliveryDisplayApplication, notice: str = "") -> str:
    cards = application.public_cards()
    card_html: list[str] = []
    for card in cards:
        status = (
            "Acknowledged"
            if card["acknowledged"]
            else "Expired"
            if card["expired"]
            else "Waiting for acknowledgement"
        )
        status_class = "done" if card["acknowledged"] else "expired" if card["expired"] else "waiting"
        button = (
            '<button type="button" disabled>Acknowledged</button>'
            if card["acknowledged"]
            else '<button type="button" disabled>Command expired</button>'
            if card["expired"]
            else (
                f'<button type="submit">Acknowledge delivery</button>'
            )
        )
        form = (
            f'<form method="post" action="/api/deliveries/{html.escape(card["commandId"])}/ack">{button}</form>'
        )
        notes = (
            f'<div class="notes"><strong>Notes</strong><p>{html.escape(str(card["notes"]))}</p></div>'
            if card["notes"]
            else ""
        )
        weight = "" if card["weightKg"] is None else f'{card["weightKg"]} kg'
        acknowledged_at = (
            f'<small>Acknowledged at {html.escape(str(card["acknowledgedAt"]))}</small>'
            if card["acknowledgedAt"]
            else f'<small>Expires at {html.escape(str(card["expiresAt"]))}</small>'
        )
        card_html.append(
            f"""
            <article class="card">
              <div class="card-head">
                <div><span class="eyebrow">Delivery</span><h2>{html.escape(str(card['trackingCode']))}</h2></div>
                <span class="status {status_class}">{status}</span>
              </div>
              <div class="route">
                <div><span>Pickup</span><strong>{html.escape(str(card['sourceName']))}</strong></div>
                <b>&rarr;</b>
                <div><span>Destination</span><strong>{html.escape(str(card['destinationName']))}</strong></div>
              </div>
              <dl>
                <div><dt>Item</dt><dd>{html.escape(str(card['itemName']))}</dd></div>
                <div><dt>Package</dt><dd>{html.escape(str(card['category']))} {html.escape(weight)}</dd></div>
                <div><dt>Priority</dt><dd>{html.escape(str(card['priority']))}</dd></div>
                <div><dt>Recipient</dt><dd>{html.escape(str(card['recipientName']))}</dd></div>
                <div><dt>Phone</dt><dd>{html.escape(str(card['recipientPhone']))}</dd></div>
                <div><dt>Requested by</dt><dd>{html.escape(str(card['requesterName']))}</dd></div>
              </dl>
              {notes}
              <div class="card-foot">{acknowledged_at}{form}</div>
            </article>
            """
        )

    notice_html = f'<div class="notice">{html.escape(notice)}</div>' if notice else ""
    empty = (
        '<section class="empty"><h2>No delivery received yet</h2><p>Keep this page open. New deliveries appear automatically.</p></section>'
        if not card_html
        else ""
    )
    connection = "Connected to EMQX" if application.connected.is_set() else "Connecting to EMQX"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MIIT Raspberry Pi Delivery Display</title>
  <style>
    :root {{ font-family: Inter, system-ui, sans-serif; color: #19352c; background: #eef5f1; }}
    * {{ box-sizing: border-box; }} body {{ margin: 0; }}
    header {{ padding: 24px clamp(18px,4vw,48px); color: white; background: #173d31; display:flex; justify-content:space-between; gap:20px; align-items:center; }}
    header h1 {{ margin: 4px 0 0; font-size: clamp(23px,4vw,38px); }} header p {{ margin:0; color:#b9d4ca; }}
    .connection {{ padding:9px 13px; border-radius:999px; background:#28594a; white-space:nowrap; }}
    main {{ width:min(1080px,calc(100% - 28px)); margin:24px auto 60px; }}
    .notice {{ margin-bottom:16px; padding:13px 16px; border-radius:12px; background:#d9f2e5; color:#155c3c; font-weight:700; }}
    .grid {{ display:grid; gap:18px; }} .card,.empty {{ padding:22px; border:1px solid #d8e5df; border-radius:18px; background:white; box-shadow:0 12px 35px rgba(21,61,49,.08); }}
    .card-head,.card-foot,.route {{ display:flex; align-items:center; justify-content:space-between; gap:18px; }}
    .eyebrow,dt,.route span {{ color:#758981; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.08em; }}
    h2 {{ margin:5px 0 0; }} .status {{ padding:8px 11px; border-radius:999px; font-weight:800; font-size:13px; }}
    .status.waiting {{ color:#795400; background:#fff0c8; }} .status.done {{ color:#13603d; background:#d9f2e5; }} .status.expired {{ color:#8b2e2e; background:#fbe0e0; }}
    .route {{ margin:20px 0; padding:18px; border-radius:14px; background:#f2f7f4; }} .route div {{ flex:1; }} .route span,.route strong {{ display:block; }} .route strong {{ margin-top:5px; }}
    dl {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:0; }} dl div {{ padding:12px; border:1px solid #e3ebe7; border-radius:12px; }} dd {{ margin:5px 0 0; font-weight:750; overflow-wrap:anywhere; }}
    .notes {{ margin-top:14px; padding:14px; border-left:4px solid #84b8a4; background:#f7faf8; }} .notes p {{ margin:6px 0 0; }}
    .card-foot {{ margin-top:18px; }} .card-foot small {{ color:#718078; overflow-wrap:anywhere; }}
    button {{ border:0; border-radius:12px; padding:13px 20px; color:white; background:#1d6a50; font:inherit; font-weight:850; cursor:pointer; }} button:hover {{ background:#15533e; }} button:disabled {{ cursor:not-allowed; color:#708078; background:#dfe7e3; }}
    @media (max-width:700px) {{ header,.card-head,.card-foot,.route {{ align-items:stretch; flex-direction:column; }} dl {{ grid-template-columns:1fr; }} form button {{ width:100%; }} }}
  </style>
</head>
<body>
  <header><div><p>MIIT Rover &middot; Raspberry Pi</p><h1>Delivery information</h1></div><span class="connection">{connection}</span></header>
  <main>{notice_html}<div class="grid">{''.join(card_html)}{empty}</div></main>
  <script>window.setTimeout(() => window.location.reload(), 5000);</script>
</body>
</html>"""


def make_http_handler(application: DeliveryDisplayApplication):
    class DeliveryDisplayHandler(BaseHTTPRequestHandler):
        server_version = "MIITDeliveryDisplay/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            if parsed.path == "/":
                notice = parse_qs(parsed.query).get("notice", [""])[0]
                self._send_html(render_page(application, notice))
                return
            if parsed.path == "/api/deliveries":
                self._send_json({"deliveries": application.public_cards()})
                return
            if parsed.path == "/health":
                self._send_json(
                    {
                        "ok": True,
                        "mqttConnected": application.connected.is_set(),
                        "mqttSubscribed": application.subscribed.is_set(),
                        "robotId": application.config.robot_id,
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            match = ACK_PATH.fullmatch(parsed.path)
            if not match:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if int(self.headers.get("Content-Length", "0") or "0") > 4096:
                self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            try:
                application.acknowledge_delivery(match.group(1))
            except KeyError as exc:
                self._send_html(render_page(application, str(exc)), HTTPStatus.NOT_FOUND)
                return
            except (RuntimeError, TimeoutError, ValueError) as exc:
                self._send_html(render_page(application, str(exc)), HTTPStatus.CONFLICT)
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/?notice=Delivery+acknowledgement+sent")
            self.end_headers()

        def _send_html(
            self, content: str, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, content: dict[str, Any]) -> None:
            encoded = json.dumps(content, separators=(",", ":")).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, message_format: str, *args: object) -> None:
            LOGGER.info("http " + message_format, *args)

    return DeliveryDisplayHandler


def build_mqtt_client(application: DeliveryDisplayApplication) -> mqtt.Client:
    config = application.config
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.mqtt_client_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
    )
    client.manual_ack_set(True)
    client.username_pw_set(config.mqtt_username, config.mqtt_password)
    client.tls_set(
        ca_certs=config.mqtt_ca_file,
        cert_reqs=ssl.CERT_REQUIRED,
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.will_set(
        application.presence_topic,
        json.dumps(application.presence_payload(False), separators=(",", ":")),
        qos=1,
        retain=True,
    )

    def on_connect(
        active_client: mqtt.Client,
        _userdata: object,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            LOGGER.error("mqtt_connection_failed reason=%s", reason_code)
            return
        application.connected.set()
        application.subscribed.clear()
        result, message_id = active_client.subscribe(application.command_topic, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error("mqtt_subscribe_failed result=%s", result)
            active_client.disconnect()
            return
        LOGGER.info("mqtt_connected subscribe_mid=%s", message_id)

    def on_subscribe(
        _active_client: mqtt.Client,
        _userdata: object,
        _message_id: int,
        reason_codes: list[mqtt.ReasonCode],
        _properties: mqtt.Properties | None,
    ) -> None:
        if any(code.is_failure for code in reason_codes):
            LOGGER.error("mqtt_subscription_rejected reasons=%s", reason_codes)
            return
        application.subscribed.set()
        application.publish_presence(True)
        LOGGER.info("mqtt_command_subscription_ready topic=%s", application.command_topic)

    def on_disconnect(
        _active_client: mqtt.Client,
        _userdata: object,
        _flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        application.connected.clear()
        application.subscribed.clear()
        if not application.stopping.is_set():
            LOGGER.warning("mqtt_disconnected reason=%s", reason_code)

    def on_message(
        active_client: mqtt.Client,
        _userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        durable = False
        try:
            application.receive_command(
                topic=message.topic,
                qos=message.qos,
                retain=bool(message.retain),
                payload=message.payload,
            )
            durable = True
        except Exception as exc:
            LOGGER.error("delivery_rejected reason=%s", type(exc).__name__)
        if durable and message.qos > 0:
            result = active_client.ack(message.mid, message.qos)
            if result != mqtt.MQTT_ERR_SUCCESS:
                LOGGER.error("mqtt_inbound_ack_failed result=%s", result)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def presence_loop(application: DeliveryDisplayApplication) -> None:
    while not application.stopping.wait(application.config.presence_interval_seconds):
        application.publish_presence(True)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("ROBOT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = DisplayConfig.from_environment()
    store = DeliveryStore(config.database_path)
    application = DeliveryDisplayApplication(config, store)
    client = build_mqtt_client(application)
    application.mqtt_client = client
    server = ThreadingHTTPServer(
        (config.bind_host, config.http_port), make_http_handler(application)
    )
    server.timeout = 0.5

    def stop(_signum: int, _frame: object) -> None:
        application.stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    publisher = threading.Thread(
        target=presence_loop,
        args=(application,),
        name="display-presence-publisher",
        daemon=True,
    )
    LOGGER.info(
        "delivery_display_starting robot_id=%s address=%s:%s database=%s",
        config.robot_id,
        config.bind_host,
        config.http_port,
        config.database_path,
    )
    try:
        client.connect(config.mqtt_host, config.mqtt_port, keepalive=30)
        client.loop_start()
        publisher.start()
        while not application.stopping.is_set():
            server.handle_request()
    finally:
        application.stopping.set()
        application.publish_presence(False)
        if client.is_connected():
            client.disconnect()
        client.loop_stop()
        server.server_close()
        store.close()
        LOGGER.info("delivery_display_stopped robot_id=%s", config.robot_id)


if __name__ == "__main__":
    main()

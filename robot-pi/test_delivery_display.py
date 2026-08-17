from __future__ import annotations

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


def _install_mqtt_stub() -> None:
    """Keep unit tests runnable without installing the Pi-only MQTT package."""

    try:
        import paho.mqtt.client as mqtt_client
    except ModuleNotFoundError:
        paho = types.ModuleType("paho")
        mqtt_package = types.ModuleType("paho.mqtt")
        mqtt_client = types.ModuleType("paho.mqtt.client")
        paho.mqtt = mqtt_package
        mqtt_package.client = mqtt_client
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = mqtt_package
        sys.modules["paho.mqtt.client"] = mqtt_client

    # test_agent may have installed a smaller process-wide stub first. Add the
    # display service's required surface without depending on discovery order.
    required_attributes = {
        "MQTT_ERR_SUCCESS": 0,
        "MQTT_ERR_NO_CONN": 4,
        "MQTTv311": 4,
        "CallbackAPIVersion": types.SimpleNamespace(VERSION2=2),
        "Client": object,
        "MQTTMessage": object,
    }
    for name, value in required_attributes.items():
        if not hasattr(mqtt_client, name):
            setattr(mqtt_client, name, value)


_install_mqtt_stub()

import paho.mqtt.client as mqtt  # noqa: E402

from delivery_display import (
    DeliveryDisplayApplication,
    DeliveryStore,
    DisplayConfig,
    build_mqtt_client,
    make_http_handler,
    render_page,
)


ROBOT_ID = "robot-01"
DELIVERY_ID = "11111111-1111-4111-8111-111111111111"
COMMAND_ID = "22222222-2222-4222-8222-222222222222"


class FakePublishInfo:
    def __init__(self, *, rc: int = mqtt.MQTT_ERR_SUCCESS, confirmed: bool = True):
        self.rc = rc
        self.confirmed = confirmed
        self.wait_timeout: float | None = None

    def wait_for_publish(self, timeout: float | None = None) -> None:
        self.wait_timeout = timeout

    def is_published(self) -> bool:
        return self.confirmed


class FakeMqttClient:
    def __init__(
        self,
        *,
        connected: bool = True,
        publish_rc: int = mqtt.MQTT_ERR_SUCCESS,
        confirmed: bool = True,
    ):
        self.connected = connected
        self.publish_rc = publish_rc
        self.confirmed = confirmed
        self.published: list[dict[str, object]] = []

    def is_connected(self) -> bool:
        return self.connected

    def publish(
        self,
        topic: str,
        payload: str,
        *,
        qos: int,
        retain: bool,
    ) -> FakePublishInfo:
        info = FakePublishInfo(rc=self.publish_rc, confirmed=self.confirmed)
        self.published.append(
            {
                "topic": topic,
                "payload": payload,
                "qos": qos,
                "retain": retain,
                "info": info,
            }
        )
        return info


class FakeConstructedMqttClient:
    """Records Paho client configuration and broker-level PUBACK calls."""

    def __init__(self):
        self.manual_ack_enabled = False
        self.credentials: tuple[str, str] | None = None
        self.tls_options: dict[str, object] | None = None
        self.reconnect_delay: tuple[int, int] | None = None
        self.will: dict[str, object] | None = None
        self.acks: list[tuple[int, int]] = []
        self.on_connect = None
        self.on_subscribe = None
        self.on_disconnect = None
        self.on_message = None

    def manual_ack_set(self, enabled: bool) -> None:
        self.manual_ack_enabled = enabled

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def tls_set(self, **options: object) -> None:
        self.tls_options = options

    def reconnect_delay_set(self, *, min_delay: int, max_delay: int) -> None:
        self.reconnect_delay = (min_delay, max_delay)

    def will_set(self, topic: str, payload: str, *, qos: int, retain: bool) -> None:
        self.will = {
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain,
        }

    def ack(self, message_id: int, qos: int) -> int:
        self.acks.append((message_id, qos))
        return mqtt.MQTT_ERR_SUCCESS


def display_config(database_path: Path) -> DisplayConfig:
    return DisplayConfig(
        robot_id=ROBOT_ID,
        mqtt_host="broker.example.test",
        mqtt_port=8883,
        mqtt_username=ROBOT_ID,
        mqtt_password="test-only-password",
        mqtt_ca_file="test-ca.pem",
        mqtt_client_id=f"{ROBOT_ID}-pi",
        bind_host="127.0.0.1",
        http_port=0,
        database_path=database_path,
        version="pi-delivery-display-test",
        presence_interval_seconds=15,
    )


def delivery_command(
    now: datetime,
    *,
    command_id: str = COMMAND_ID,
    delivery_id: str = DELIVERY_ID,
    expires_at: datetime | None = None,
    delivery_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    delivery = {
        "trackingCode": "MIIT-1065",
        "requesterName": "Campus User",
        "requesterEmail": "user@miit.edu.mm",
        "recipientName": "Library Desk",
        "recipientPhone": "+95 9 123 456 789",
        "sourceName": "Faculty of Computer Science",
        "destinationName": "Central Library",
        "itemName": "Prototype parcel",
        "category": "Documents",
        "weightKg": 1.5,
        "priority": "HIGH",
        "notes": "Handle with care.",
    }
    delivery.update(delivery_overrides or {})
    return {
        "schemaVersion": 1,
        "commandId": command_id,
        "robotId": ROBOT_ID,
        "command": "START_MISSION",
        "payload": {
            "sourceLocationId": "loc-fcs",
            "destinationLocationId": "loc-library",
            "mapVersion": "miit-campus-v1",
            "deliveryId": delivery_id,
            "deliveryMode": "ACKNOWLEDGEMENT_ONLY",
            "delivery": delivery,
        },
        "issuedAt": now.isoformat(),
        "expiresAt": (expires_at or now + timedelta(hours=1)).isoformat(),
    }


class DeliveryDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "display.db"
        self.store = DeliveryStore(self.database_path)
        self.mqtt_client = FakeMqttClient()
        self.application = DeliveryDisplayApplication(
            display_config(self.database_path),
            self.store,
            self.mqtt_client,
        )
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self) -> None:
        try:
            self.store.close()
        except sqlite3.ProgrammingError:
            # A recovery test may deliberately close the original connection.
            pass
        self.temporary_directory.cleanup()

    def receive(self, command: dict[str, object] | None = None) -> tuple[str, bool]:
        return self.application.receive_command(
            topic=self.application.command_topic,
            qos=1,
            retain=False,
            payload=json.dumps(command or delivery_command(self.now)).encode("utf-8"),
            now=self.now,
        )

    def test_receive_persists_full_delivery_without_automatic_application_ack(self):
        command_id, inserted = self.receive()

        self.assertEqual(command_id, COMMAND_ID)
        self.assertTrue(inserted)
        self.assertEqual(self.mqtt_client.published, [])
        cards = self.application.public_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["trackingCode"], "MIIT-1065")
        self.assertEqual(cards[0]["sourceName"], "Faculty of Computer Science")
        self.assertEqual(cards[0]["destinationName"], "Central Library")
        self.assertEqual(cards[0]["recipientPhone"], "+95 9 123 456 789")
        self.assertEqual(cards[0]["weightKg"], 1.5)
        self.assertFalse(cards[0]["acknowledged"])
        self.assertFalse(cards[0]["expired"])

        duplicate_id, duplicate_inserted = self.receive()
        self.assertEqual(duplicate_id, COMMAND_ID)
        self.assertFalse(duplicate_inserted)
        self.assertEqual(len(self.store.list_cards()), 1)

    def test_receive_rejects_conflicting_duplicate_and_invalid_transport(self):
        self.receive()
        conflicting = delivery_command(
            self.now,
            delivery_overrides={"trackingCode": "MIIT-CONFLICT"},
        )
        with self.assertRaisesRegex(ValueError, "reused with different delivery data"):
            self.receive(conflicting)

        raw = json.dumps(delivery_command(self.now))
        transports = [
            {
                "topic": "miit/robots/robot-02/commands",
                "qos": 1,
                "retain": False,
            },
            {
                "topic": self.application.command_topic,
                "qos": 0,
                "retain": False,
            },
            {
                "topic": self.application.command_topic,
                "qos": 1,
                "retain": True,
            },
        ]
        for transport in transports:
            with self.subTest(transport=transport):
                with self.assertRaises(ValueError):
                    self.application.receive_command(
                        payload=raw,
                        now=self.now,
                        **transport,
                    )

    def test_receive_rejects_expired_non_mission_and_malformed_commands(self):
        expired = delivery_command(
            self.now - timedelta(minutes=10),
            expires_at=self.now - timedelta(minutes=1),
        )
        with self.assertRaisesRegex(ValueError, "already expired"):
            self.receive(expired)

        pause = delivery_command(self.now)
        pause["command"] = "PAUSE"
        pause["payload"] = {"reason": "Prototype test"}
        pause["expiresAt"] = (self.now + timedelta(minutes=5)).isoformat()
        with self.assertRaisesRegex(ValueError, "only START_MISSION"):
            self.receive(pause)

        with self.assertRaises(json.JSONDecodeError):
            self.application.receive_command(
                topic=self.application.command_topic,
                qos=1,
                retain=False,
                payload=b"{not-json",
                now=self.now,
            )

    def test_mqtt_callback_pubacks_only_after_the_command_is_durable(self):
        constructed = FakeConstructedMqttClient()
        with mock.patch("delivery_display.mqtt.Client", return_value=constructed):
            client = build_mqtt_client(self.application)

        self.assertIs(client, constructed)
        self.assertTrue(constructed.manual_ack_enabled)
        self.assertEqual(
            constructed.credentials,
            (ROBOT_ID, "test-only-password"),
        )
        self.assertEqual(constructed.will["topic"], self.application.presence_topic)
        self.assertEqual(constructed.will["qos"], 1)
        self.assertTrue(constructed.will["retain"])

        valid_message = types.SimpleNamespace(
            topic=self.application.command_topic,
            qos=1,
            retain=False,
            payload=json.dumps(delivery_command(self.now)).encode("utf-8"),
            mid=41,
        )
        with mock.patch(
            "delivery_display.datetime",
            wraps=datetime,
        ) as clock:
            clock.now.return_value = self.now
            constructed.on_message(constructed, None, valid_message)

        self.assertEqual(constructed.acks, [(41, 1)])
        self.assertIsNotNone(self.store.get_card(COMMAND_ID))
        self.assertEqual(self.mqtt_client.published, [])

        invalid_message = types.SimpleNamespace(
            topic=self.application.command_topic,
            qos=0,
            retain=False,
            payload=json.dumps(delivery_command(self.now)).encode("utf-8"),
            mid=42,
        )
        constructed.on_message(constructed, None, invalid_message)
        self.assertEqual(constructed.acks, [(41, 1)])

    def test_ack_button_publishes_exact_terminal_receipt_once_and_persists_it(self):
        self.receive()

        acknowledgement = self.application.acknowledge_delivery(
            COMMAND_ID,
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(
            acknowledgement,
            {
                "schemaVersion": 1,
                "commandId": COMMAND_ID,
                "robotId": ROBOT_ID,
                "status": "COMPLETED",
                "reason": "Delivery information acknowledged on Raspberry Pi display",
                "at": (self.now + timedelta(minutes=1)).isoformat(),
            },
        )
        self.assertEqual(len(self.mqtt_client.published), 1)
        publication = self.mqtt_client.published[0]
        self.assertEqual(publication["topic"], f"miit/robots/{ROBOT_ID}/acks")
        self.assertEqual(publication["qos"], 1)
        self.assertFalse(publication["retain"])
        self.assertEqual(json.loads(str(publication["payload"])), acknowledgement)
        self.assertEqual(publication["info"].wait_timeout, 10)

        duplicate = self.application.acknowledge_delivery(
            COMMAND_ID,
            now=self.now + timedelta(minutes=2),
        )
        self.assertEqual(duplicate, acknowledgement)
        self.assertEqual(len(self.mqtt_client.published), 1)
        self.assertTrue(self.application.public_cards()[0]["acknowledged"])

        self.store.close()
        recovered = DeliveryStore(self.database_path)
        try:
            recovered_card = recovered.get_card(COMMAND_ID)
            self.assertIsNotNone(recovered_card)
            self.assertEqual(recovered_card["acknowledgement"], acknowledgement)
        finally:
            recovered.close()

    def test_ack_failure_never_marks_the_delivery_acknowledged(self):
        self.receive()

        self.mqtt_client.connected = False
        with self.assertRaisesRegex(RuntimeError, "MQTT is disconnected"):
            self.application.acknowledge_delivery(COMMAND_ID, now=self.now)
        self.assertIsNone(self.store.get_card(COMMAND_ID)["acknowledgement"])

        self.mqtt_client.connected = True
        self.mqtt_client.publish_rc = mqtt.MQTT_ERR_NO_CONN
        with self.assertRaisesRegex(RuntimeError, "MQTT publish failed"):
            self.application.acknowledge_delivery(COMMAND_ID, now=self.now)
        self.assertIsNone(self.store.get_card(COMMAND_ID)["acknowledgement"])

        self.mqtt_client.publish_rc = mqtt.MQTT_ERR_SUCCESS
        self.mqtt_client.confirmed = False
        with self.assertRaisesRegex(TimeoutError, "did not confirm"):
            self.application.acknowledge_delivery(COMMAND_ID, now=self.now)
        self.assertIsNone(self.store.get_card(COMMAND_ID)["acknowledgement"])

    def test_ack_rejects_unknown_or_expired_delivery(self):
        with self.assertRaisesRegex(KeyError, "not found"):
            self.application.acknowledge_delivery(COMMAND_ID, now=self.now)

        command = delivery_command(self.now, expires_at=self.now + timedelta(minutes=1))
        self.receive(command)
        with self.assertRaisesRegex(ValueError, "expired before acknowledgement"):
            self.application.acknowledge_delivery(
                COMMAND_ID,
                now=self.now + timedelta(minutes=2),
            )
        self.assertEqual(self.mqtt_client.published, [])

    def test_rendered_page_escapes_delivery_content(self):
        self.receive(
            delivery_command(
                self.now,
                delivery_overrides={"notes": "<script>alert('unsafe')</script>"},
            )
        )

        page = render_page(self.application)

        self.assertIn("MIIT-1065", page)
        self.assertIn("Acknowledge delivery", page)
        self.assertNotIn("<script>alert('unsafe')</script>", page)
        self.assertIn("&lt;script&gt;alert(&#x27;unsafe&#x27;)&lt;/script&gt;", page)

    def test_http_endpoints_list_and_acknowledge_delivery(self):
        self.receive()
        self.application.connected.set()
        self.application.subscribed.set()
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_http_handler(self.application),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_address[1],
            timeout=5,
        )
        try:
            connection.request("GET", "/api/deliveries")
            response = connection.getresponse()
            content = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(content["deliveries"][0]["commandId"], COMMAND_ID)

            connection.request("GET", "/health")
            response = connection.getresponse()
            health = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(
                health,
                {
                    "ok": True,
                    "mqttConnected": True,
                    "mqttSubscribed": True,
                    "robotId": ROBOT_ID,
                },
            )

            connection.request(
                "POST",
                f"/api/deliveries/{COMMAND_ID}/ack",
                body=b"",
                headers={"Content-Length": "0"},
            )
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 303)
            self.assertEqual(
                response.getheader("Location"),
                "/?notice=Delivery+acknowledgement+sent",
            )
            self.assertEqual(len(self.mqtt_client.published), 1)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_presence_uses_existing_robot_identity_and_retained_topic(self):
        self.application.publish_presence(True)

        self.assertEqual(len(self.mqtt_client.published), 1)
        publication = self.mqtt_client.published[0]
        self.assertEqual(publication["topic"], f"miit/robots/{ROBOT_ID}/presence")
        self.assertEqual(publication["qos"], 1)
        self.assertTrue(publication["retain"])
        payload = json.loads(str(publication["payload"]))
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["robotId"], ROBOT_ID)
        self.assertTrue(payload["online"])
        self.assertEqual(payload["firmwareVersion"], "pi-delivery-display-test")


class DisplayConfigTests(unittest.TestCase):
    def test_environment_defaults_match_existing_emqx_acl_identity(self):
        environment = {
            "ROBOT_ID": ROBOT_ID,
            "MQTT_HOST": "broker.example.test",
            "MQTT_USERNAME": ROBOT_ID,
            "MQTT_PASSWORD": "test-only-password",
            "MQTT_CA_FILE": "/tmp/test-ca.pem",
            "ROBOT_STATE_DIR": "/tmp/miit-display-test",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            config = DisplayConfig.from_environment()

        self.assertEqual(config.mqtt_client_id, f"{ROBOT_ID}-pi")
        self.assertEqual(config.http_port, 8080)
        self.assertEqual(config.bind_host, "0.0.0.0")
        self.assertEqual(
            config.database_path,
            Path("/tmp/miit-display-test/delivery-display.db"),
        )

    def test_environment_rejects_mismatched_mqtt_username(self):
        environment = {
            "ROBOT_ID": ROBOT_ID,
            "MQTT_HOST": "broker.example.test",
            "MQTT_USERNAME": "another-robot",
            "MQTT_PASSWORD": "test-only-password",
            "MQTT_CA_FILE": "/tmp/test-ca.pem",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must equal ROBOT_ID"):
                DisplayConfig.from_environment()
if __name__ == "__main__":
    unittest.main()

"""Regression tests for confirmed findings in mqtt_client.py.

Finding 1 – unused ``import time`` (dead import, removed).
Finding 2 – race condition: ``_on_message`` used ``get()`` instead of ``pop()``
    so the pending entry lingered and could be processed twice (duplicate
    response) or raced with the ``finally`` cleanup in ``request()``.
Finding 3 – ``wait_for_publish`` failure was silently swallowed; paho raises on
    failure so we now catch and convert to ``MqttError``.
"""

from __future__ import annotations

import json
import threading
import time
import pytest

from cli_anything.zigbee2mqtt.tests.test_core import FakeMqttClient, fake_paho


class _FakeInfo:
    """Stand-in for paho's MQTTMessageInfo — wait_for_publish returns None."""
    rc = 0

    def wait_for_publish(self, timeout=None):
        return None


# ── Finding 1: dead import removed ──────────────────────────────────────────

class TestUnusedImportTime:
    """Verify ``import time`` is not present in mqtt_client.py."""

    def test_no_time_import(self):
        import cli_anything.zigbee2mqtt.core.mqtt_client as mc
        import sys
        mod_src = sys.modules[mc.__name__].__file__
        assert mod_src is not None
        with open(mod_src) as fh:
            src = fh.read()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("import time") or stripped.startswith("from time import"):
                pytest.fail(f"Found leftover time import: {line!r}")


# ── Finding 3: wait_for_publish failure surfaced as MqttError ───────────────

class TestWaitForPublishChecked:
    """Verify BridgeClient.request raises MqttError when publish itself fails."""

    def test_request_raises_when_publish_fails(self, fake_paho, monkeypatch):
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        def bad_publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))

            class BadInfo:
                rc = 4
                def wait_for_publish(self, timeout=None):
                    raise RuntimeError("boom")
            return BadInfo()

        monkeypatch.setattr(FakeMqttClient, "publish", bad_publish, raising=True)
        c = mc.BridgeClient("fake-host", base_topic="z2m")
        with c as client:
            with pytest.raises(mc.MqttError, match="publish failed"):
                client.request("device/rename", payload={"from": "A", "to": "B"})


# ── Finding 2: race condition — pop() prevents duplicate processing ─────────

class TestSlotRaceCondition:
    """Verify that ``_on_message`` atomically removes the pending entry so a
    duplicate response for the same transaction cannot corrupt the slot or
    re-fire the event, and that concurrent requests each get their own response.
    """

    def test_duplicate_response_does_not_overwrite(self, fake_paho, monkeypatch):
        """Simulate a broker re-sending a response twice — first ``ok``, then
        ``error``.

        With the old ``get()`` code the second invocation would find the entry
        still in ``_pending`` (because ``get`` does not remove it) and overwrite
        ``slot["response"]`` with the error, causing ``request()`` to raise.
        With ``pop()`` the second invocation finds nothing and is a no-op, so
        the original ``ok`` response is preserved.
        """
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        def dup_publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))
            if "/bridge/request/" in topic:
                req_path = topic.split("/bridge/request/", 1)[1]
                data = json.loads(payload)
                txn = data.get("transaction")
                resp_topic = topic.replace("/request/", "/response/")

                class FakeMsg:
                    def __init__(self, t, p):
                        self.topic = t
                        self.payload = json.dumps(p).encode()

                ok_resp = {"status": "ok", "data": {"echo": req_path},
                           "transaction": txn}
                err_resp = {"status": "error", "error": "duplicate",
                            "transaction": txn}

                # Deliver ok first, then a duplicate error — the error must
                # NOT overwrite the ok response.
                if self.on_message:
                    self.on_message(self, None, FakeMsg(resp_topic, ok_resp))
                    self.on_message(self, None, FakeMsg(resp_topic, err_resp))

            return _FakeInfo()

        monkeypatch.setattr(FakeMqttClient, "publish", dup_publish, raising=True)
        c = mc.BridgeClient("fake-host", base_topic="z2m")

        with c as client:
            # With pop() this returns ok; with get() the duplicate error
            # would overwrite the slot and raise MqttError.
            resp = client.request("device/rename", payload={"from": "A", "to": "B"})
            assert resp["status"] == "ok"
            assert resp["data"]["echo"] == "device/rename"
            # After request returns, the pending entry must be cleaned up
            assert len(client._pending) == 0

    def test_concurrent_requests_each_get_own_response(self, fake_paho, monkeypatch):
        """Multiple concurrent requests must each receive their own correlated
        response — no cross-talk from the race condition.

        This exercises the ``pop()`` fix under real concurrency: many threads
        call ``request()`` simultaneously, each with a unique transaction, and
        the fake broker delivers responses from a separate thread.  If
        ``_on_message`` used ``get()`` instead of ``pop()``, a response could
        be processed twice or the ``finally`` cleanup in ``request()`` could
        race with ``_on_message``, causing wrong responses or timeouts.
        """
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        # Queue of (topic, response_dict) messages to deliver asynchronously
        msg_queue: list[tuple[str, dict]] = []
        queue_lock = threading.Lock()
        stop_pump = threading.Event()

        def async_publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))
            if "/bridge/request/" in topic:
                req_path = topic.split("/bridge/request/", 1)[1]
                data = json.loads(payload)
                txn = data.get("transaction")
                resp_topic = topic.replace("/request/", "/response/")
                resp = {"status": "ok", "data": {"echo": req_path},
                        "transaction": txn}
                with queue_lock:
                    msg_queue.append((resp_topic, resp))
            return _FakeInfo()

        monkeypatch.setattr(FakeMqttClient, "publish", async_publish, raising=True)
        c = mc.BridgeClient("fake-host", base_topic="z2m")

        # Pump thread: delivers queued messages via on_message with small delays
        def pump():
            while not stop_pump.is_set():
                with queue_lock:
                    if msg_queue:
                        topic, resp = msg_queue.pop(0)
                    else:
                        topic, resp = None, None
                if topic is not None:
                    class FakeMsg:
                        def __init__(self, t, p):
                            self.topic = t
                            self.payload = json.dumps(p).encode()
                    c.client.on_message(c.client, None, FakeMsg(topic, resp))
                else:
                    time.sleep(0.001)

        with c as client:
            pump_thread = threading.Thread(target=pump, daemon=True)
            pump_thread.start()

            num_requests = 20
            results: dict[int, dict] = {}
            errors: list[Exception] = []
            results_lock = threading.Lock()

            def do_request(idx):
                try:
                    resp = client.request(
                        f"device/rename_{idx}",
                        payload={"from": f"A{idx}", "to": f"B{idx}"},
                        timeout=10.0,
                    )
                    with results_lock:
                        results[idx] = resp
                except Exception as exc:
                    with results_lock:
                        errors.append(exc)

            threads = [
                threading.Thread(target=do_request, args=(i,))
                for i in range(num_requests)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            stop_pump.set()
            pump_thread.join(timeout=5)

            assert errors == [], f"Requests failed: {errors}"
            assert len(results) == num_requests
            for idx, resp in results.items():
                assert resp["status"] == "ok"
                # Each response must be correlated to its own request
                assert resp["data"]["echo"] == f"device/rename_{idx}"

"""Unit tests for the v0.2.0 refine pass:

* bindings — device/bind, device/unbind, list_bindings
* devices extras — read_state, find_stale, generate_external_definition,
  configure_reporting
* install_code — add, remove
* groups extras — options
* extensions — list / show / save / remove

These tests use a fake `BridgeClient` that records `request()` /
`collect_retained()` calls and returns prepared responses. No MQTT
broker required.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from cli_anything.zigbee2mqtt.core import bindings as bindings_core
from cli_anything.zigbee2mqtt.core import devices as devices_core
from cli_anything.zigbee2mqtt.core import extensions as extensions_core
from cli_anything.zigbee2mqtt.core import groups as groups_core
from cli_anything.zigbee2mqtt.core import install_code as install_code_core


# ─────────────────────────────────────────────────────────── fake client

class FakeClient:
    """Minimum surface for the new core modules.

    Tests register canned responses via :meth:`set_request` and
    :meth:`set_retained`. Every call is recorded on ``self.requests`` and
    ``self.retained_calls`` for assertion.
    """

    base_topic = "zigbee2mqtt"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.retained_calls: list[str] = []
        self._request_responses: dict[str, dict] = {}
        self._retained_payloads: dict[str, str] = {}

    def set_request(self, path: str, response: dict) -> None:
        self._request_responses[path] = response

    def set_retained(self, topic: str, payload) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        self._retained_payloads[topic] = payload

    def request(self, path: str, payload=None, *, timeout: float = 0):
        self.requests.append({"path": path, "payload": payload,
                                "timeout": timeout})
        return self._request_responses.get(
            path, {"status": "ok", "data": {}}
        )

    def collect_retained(self, topic: str, *, timeout: float = 0):
        self.retained_calls.append(topic)
        return self._retained_payloads.get(topic)


@pytest.fixture
def fc() -> FakeClient:
    return FakeClient()


# ════════════════════════════════════════════════════════════════════════
# bindings
# ════════════════════════════════════════════════════════════════════════

class TestBindings:
    def test_bind_default_clusters(self, fc):
        fc.set_request("device/bind", {"status": "ok",
                                          "data": {"clusters": ["genOnOff"]}})
        result = bindings_core.bind(fc, from_="switch_kitchen", to="light_kitchen")
        call = fc.requests[0]
        assert call["path"] == "device/bind"
        assert call["payload"] == {"from": "switch_kitchen", "to": "light_kitchen"}
        assert "clusters" not in call["payload"]
        assert result["status"] == "ok"

    def test_bind_explicit_clusters(self, fc):
        fc.set_request("device/bind", {"status": "ok"})
        bindings_core.bind(fc, from_="A", to="B",
                              clusters=["genOnOff", "genLevelCtrl"])
        assert fc.requests[0]["payload"]["clusters"] == [
            "genOnOff", "genLevelCtrl"
        ]

    def test_bind_missing_from(self, fc):
        with pytest.raises(ValueError, match="from_"):
            bindings_core.bind(fc, from_="", to="B")

    def test_bind_missing_to(self, fc):
        with pytest.raises(ValueError):
            bindings_core.bind(fc, from_="A", to="")

    def test_unbind(self, fc):
        fc.set_request("device/unbind", {"status": "ok"})
        bindings_core.unbind(fc, from_="A", to="B", clusters=["genOnOff"])
        assert fc.requests[0]["path"] == "device/unbind"
        assert fc.requests[0]["payload"]["clusters"] == ["genOnOff"]

    def test_unbind_missing_args(self, fc):
        with pytest.raises(ValueError):
            bindings_core.unbind(fc, from_="", to="")

    def test_list_bindings_all(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "switch", "ieee_address": "0xAAA",
             "endpoints": {"1": {"bindings": [
                 {"cluster": "genOnOff",
                  "target": {"type": "endpoint",
                              "ieee_address": "0xBBB", "endpoint": 1}},
                 {"cluster": "genLevelCtrl",
                  "target": {"type": "group", "id": 42}},
             ]}}},
            {"friendly_name": "lamp", "ieee_address": "0xBBB",
             "endpoints": {}},
        ])
        rows = bindings_core.list_bindings(fc)
        assert len(rows) == 2
        on_off = next(r for r in rows if r["cluster"] == "genOnOff")
        assert on_off["from_device"] == "switch"
        assert on_off["from_endpoint"] == 1
        assert on_off["to_ieee"] == "0xBBB"
        assert on_off["to_device"] == "lamp"  # resolved by ieee lookup
        assert on_off["to_endpoint"] == 1
        assert on_off["to_group"] is None
        grp = next(r for r in rows if r["cluster"] == "genLevelCtrl")
        assert grp["to_type"] == "group"
        assert grp["to_group"] == 42
        assert grp["to_ieee"] is None

    def test_list_bindings_filter_by_device(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "switch", "ieee_address": "0xAAA",
             "endpoints": {"1": {"bindings": [
                 {"cluster": "genOnOff",
                  "target": {"type": "endpoint",
                              "ieee_address": "0xBBB", "endpoint": 1}},
             ]}}},
            {"friendly_name": "remote", "ieee_address": "0xCCC",
             "endpoints": {"1": {"bindings": [
                 {"cluster": "genOnOff",
                  "target": {"type": "group", "id": 7}},
             ]}}},
        ])
        rows = bindings_core.list_bindings(fc, device_ident="switch")
        assert len(rows) == 1
        assert rows[0]["from_device"] == "switch"

    def test_list_bindings_filter_by_ieee(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "switch", "ieee_address": "0xAAA",
             "endpoints": {"1": {"bindings": [
                 {"cluster": "genOnOff",
                  "target": {"type": "group", "id": 1}}]}}},
            {"friendly_name": "remote", "ieee_address": "0xCCC",
             "endpoints": {"1": {"bindings": [
                 {"cluster": "genOnOff",
                  "target": {"type": "group", "id": 2}}]}}},
        ])
        rows = bindings_core.list_bindings(fc, device_ident="0xCCC")
        assert len(rows) == 1
        assert rows[0]["from_device"] == "remote"

    def test_list_bindings_no_devices(self, fc):
        assert bindings_core.list_bindings(fc) == []

    def test_list_bindings_skips_malformed(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "x", "endpoints": "not a dict"},
            {"friendly_name": "y", "endpoints": {"1": "not a dict"}},
        ])
        assert bindings_core.list_bindings(fc) == []


# ════════════════════════════════════════════════════════════════════════
# devices extras
# ════════════════════════════════════════════════════════════════════════

class TestDevicesReadState:
    def test_read_state_happy(self, fc):
        fc.set_retained("zigbee2mqtt/Lounge Lamp",
                          {"state": "ON", "brightness": 200})
        result = devices_core.read_state(fc, "Lounge Lamp")
        assert result == {"state": "ON", "brightness": 200}
        assert fc.retained_calls == ["zigbee2mqtt/Lounge Lamp"]

    def test_read_state_no_payload(self, fc):
        assert devices_core.read_state(fc, "Missing") == {}

    def test_read_state_malformed_payload(self, fc):
        fc.set_retained("zigbee2mqtt/Weird", "not json")
        assert devices_core.read_state(fc, "Weird") == {"raw": "not json"}

    def test_read_state_empty_name(self, fc):
        with pytest.raises(ValueError, match="friendly_name"):
            devices_core.read_state(fc, "")


class TestDevicesStale:
    def _now(self):
        return _dt.datetime.now(_dt.timezone.utc)

    def test_stale_over_threshold(self, fc):
        old = (self._now() - _dt.timedelta(hours=3)).isoformat()
        fresh = (self._now() - _dt.timedelta(minutes=1)).isoformat()
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "old_dev", "ieee_address": "0xA",
             "type": "EndDevice", "last_seen": old,
             "definition": {"model": "M1"}, "power_source": "Battery"},
            {"friendly_name": "fresh_dev", "ieee_address": "0xB",
             "type": "EndDevice", "last_seen": fresh,
             "definition": {"model": "M2"}, "power_source": "Battery"},
            {"friendly_name": "coord", "ieee_address": "0xC",
             "type": "Coordinator", "last_seen": old},
        ])
        result = devices_core.find_stale(fc, threshold_minutes=60)
        assert len(result) == 1
        assert result[0]["friendly_name"] == "old_dev"
        assert result[0]["minutes_since_seen"] >= 60

    def test_stale_skip_routers(self, fc):
        old = (self._now() - _dt.timedelta(hours=3)).isoformat()
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "router1", "type": "Router",
             "last_seen": old, "definition": {}},
            {"friendly_name": "end1", "type": "EndDevice",
             "last_seen": old, "definition": {}},
        ])
        result = devices_core.find_stale(fc, threshold_minutes=60,
                                            include_routers=False)
        names = [r["friendly_name"] for r in result]
        assert names == ["end1"]

    def test_stale_skip_end_devices(self, fc):
        old = (self._now() - _dt.timedelta(hours=3)).isoformat()
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "router1", "type": "Router",
             "last_seen": old, "definition": {}},
            {"friendly_name": "end1", "type": "EndDevice",
             "last_seen": old, "definition": {}},
        ])
        result = devices_core.find_stale(fc, threshold_minutes=60,
                                            include_end_devices=False)
        assert [r["friendly_name"] for r in result] == ["router1"]

    def test_stale_sorted_by_oldest_first(self, fc):
        n = self._now()
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "d1", "type": "EndDevice",
             "last_seen": (n - _dt.timedelta(hours=2)).isoformat(),
             "definition": {}},
            {"friendly_name": "d2", "type": "EndDevice",
             "last_seen": (n - _dt.timedelta(hours=8)).isoformat(),
             "definition": {}},
            {"friendly_name": "d3", "type": "EndDevice",
             "last_seen": (n - _dt.timedelta(hours=5)).isoformat(),
             "definition": {}},
        ])
        result = devices_core.find_stale(fc, threshold_minutes=60)
        assert [r["friendly_name"] for r in result] == ["d2", "d3", "d1"]

    def test_stale_unparseable_last_seen(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "weird", "type": "EndDevice",
             "last_seen": "tomorrow", "definition": {}},
        ])
        assert devices_core.find_stale(fc, threshold_minutes=60) == []

    def test_stale_missing_last_seen(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/devices", [
            {"friendly_name": "never_seen", "type": "EndDevice",
             "definition": {}},
        ])
        assert devices_core.find_stale(fc, threshold_minutes=60) == []


class TestGenerateExternalDefinition:
    def test_happy(self, fc):
        fc.set_request("device/generate_external_definition", {
            "status": "ok",
            "source": "module.exports = { ... }",
            "model": "ZX-100",
        })
        result = devices_core.generate_external_definition(fc, "kitchen-sensor")
        assert result["source"].startswith("module.exports")
        call = fc.requests[0]
        assert call["path"] == "device/generate_external_definition"
        assert call["payload"] == {"id": "kitchen-sensor"}

    def test_empty_id_raises(self, fc):
        with pytest.raises(ValueError, match="id_"):
            devices_core.generate_external_definition(fc, "")


class TestConfigureReporting:
    def test_happy_with_change(self, fc):
        fc.set_request("device/configure_reporting", {"status": "ok"})
        devices_core.configure_reporting(
            fc,
            id_="kitchen-temp", cluster="msTemperatureMeasurement",
            attribute="measuredValue",
            minimum_report_interval=30, maximum_report_interval=3600,
            reportable_change=10, endpoint=1,
        )
        call = fc.requests[0]
        assert call["path"] == "device/configure_reporting"
        assert call["payload"] == {
            "id": "kitchen-temp",
            "cluster": "msTemperatureMeasurement",
            "attribute": "measuredValue",
            "minimum_report_interval": 30,
            "maximum_report_interval": 3600,
            "reportable_change": 10,
            "endpoint": 1,
        }

    def test_happy_without_change(self, fc):
        fc.set_request("device/configure_reporting", {"status": "ok"})
        devices_core.configure_reporting(
            fc, id_="d1", cluster="genOnOff", attribute="onOff",
            minimum_report_interval=0, maximum_report_interval=300,
        )
        payload = fc.requests[0]["payload"]
        assert "reportable_change" not in payload
        assert "endpoint" not in payload

    def test_rejects_missing_id(self, fc):
        with pytest.raises(ValueError):
            devices_core.configure_reporting(
                fc, id_="", cluster="x", attribute="y",
                minimum_report_interval=0, maximum_report_interval=0,
            )

    def test_rejects_missing_cluster_or_attribute(self, fc):
        with pytest.raises(ValueError, match="cluster"):
            devices_core.configure_reporting(
                fc, id_="d", cluster="", attribute="y",
                minimum_report_interval=0, maximum_report_interval=0,
            )
        with pytest.raises(ValueError, match="cluster"):
            devices_core.configure_reporting(
                fc, id_="d", cluster="c", attribute="",
                minimum_report_interval=0, maximum_report_interval=0,
            )

    def test_rejects_negative_intervals(self, fc):
        with pytest.raises(ValueError, match="intervals"):
            devices_core.configure_reporting(
                fc, id_="d", cluster="c", attribute="a",
                minimum_report_interval=-1, maximum_report_interval=10,
            )


# ════════════════════════════════════════════════════════════════════════
# install_code
# ════════════════════════════════════════════════════════════════════════

class TestInstallCode:
    def test_add(self, fc):
        fc.set_request("install_code/add", {"status": "ok"})
        install_code_core.add(fc, "QRCODE-TEXT-HERE")
        assert fc.requests[0]["path"] == "install_code/add"
        assert fc.requests[0]["payload"] == {"value": "QRCODE-TEXT-HERE"}

    def test_add_empty_value(self, fc):
        with pytest.raises(ValueError, match="value"):
            install_code_core.add(fc, "")

    def test_remove(self, fc):
        fc.set_request("install_code/remove", {"status": "ok"})
        install_code_core.remove(fc, "QRCODE-TEXT-HERE")
        assert fc.requests[0]["path"] == "install_code/remove"
        assert fc.requests[0]["payload"] == {"value": "QRCODE-TEXT-HERE"}

    def test_remove_empty_value(self, fc):
        with pytest.raises(ValueError):
            install_code_core.remove(fc, "")


# ════════════════════════════════════════════════════════════════════════
# groups.options
# ════════════════════════════════════════════════════════════════════════

class TestGroupOptions:
    def test_happy(self, fc):
        fc.set_request("group/options", {"status": "ok"})
        groups_core.options(fc, "kitchen-lights",
                              {"transition": 1.5, "retain": True})
        call = fc.requests[0]
        assert call["path"] == "group/options"
        assert call["payload"] == {
            "id": "kitchen-lights",
            "options": {"transition": 1.5, "retain": True},
        }

    def test_missing_id(self, fc):
        with pytest.raises(ValueError):
            groups_core.options(fc, "", {"transition": 1})

    def test_non_dict_options(self, fc):
        with pytest.raises(ValueError):
            groups_core.options(fc, "g", "not a dict")  # type: ignore[arg-type]


# ════════════════════════════════════════════════════════════════════════
# extensions
# ════════════════════════════════════════════════════════════════════════

class TestExtensions:
    SAMPLE = [
        {"name": "auto-rename.js", "code": "module.exports = class {};"},
        {"name": "log-tap.js", "code": "module.exports = function() {};"},
    ]

    def test_list_happy(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/extensions", self.SAMPLE)
        result = extensions_core.list_extensions(fc)
        assert len(result) == 2
        assert result[0]["name"] == "auto-rename.js"

    def test_list_empty(self, fc):
        assert extensions_core.list_extensions(fc) == []

    def test_list_malformed(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/extensions", "not json")
        assert extensions_core.list_extensions(fc) == []

    def test_show_found(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/extensions", self.SAMPLE)
        ext = extensions_core.show(fc, "log-tap.js")
        assert ext is not None
        assert ext["code"].startswith("module.exports")

    def test_show_missing(self, fc):
        fc.set_retained("zigbee2mqtt/bridge/extensions", self.SAMPLE)
        assert extensions_core.show(fc, "no-such.js") is None

    def test_show_empty_name(self, fc):
        with pytest.raises(ValueError):
            extensions_core.show(fc, "")

    def test_save_happy(self, fc):
        fc.set_request("extension/save", {"status": "ok"})
        extensions_core.save(fc, name="my-ext.js",
                                code="module.exports = class {};")
        call = fc.requests[0]
        assert call["path"] == "extension/save"
        assert call["payload"]["name"] == "my-ext.js"
        assert call["payload"]["code"].startswith("module.exports")

    def test_save_rejects_non_js_name(self, fc):
        with pytest.raises(ValueError, match=".js"):
            extensions_core.save(fc, name="bad", code="x")

    def test_save_rejects_empty_code(self, fc):
        with pytest.raises(ValueError, match="code"):
            extensions_core.save(fc, name="x.js", code="")

    def test_save_from_file(self, fc, tmp_path):
        f = tmp_path / "ext.js"
        f.write_text("module.exports = function(){};")
        fc.set_request("extension/save", {"status": "ok"})
        extensions_core.save_from_file(fc, name="ext.js",
                                          local_path=str(f))
        assert "function" in fc.requests[0]["payload"]["code"]

    def test_remove_happy(self, fc):
        fc.set_request("extension/remove", {"status": "ok"})
        extensions_core.remove(fc, "ext.js")
        call = fc.requests[0]
        assert call["path"] == "extension/remove"
        assert call["payload"] == {"name": "ext.js"}

    def test_remove_empty(self, fc):
        with pytest.raises(ValueError):
            extensions_core.remove(fc, "")

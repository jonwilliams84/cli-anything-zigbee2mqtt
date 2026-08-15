"""Raw Zigbee cluster attribute access — read / write arbitrary attributes.

Some attributes a device implements are not modelled by its
``zigbee-herdsman-converters`` definition, so they never appear in the
``exposes`` block and there is no friendly property to ``device set``.
z2m still lets you reach them: publishing to the device command topic with a
``read`` or ``write`` key makes it issue the corresponding ZCL command::

    <base>/<friendly_name>/set          # or <base>/<friendly_name>/<endpoint>/set

    {"read":  {"cluster": "genBasic", "attributes": ["zclVersion"]}}
    {"write": {"cluster": "genOnOff", "payload": {"onOff": 1}}}

Both accept an ``options`` object for ZCL frame control —
``manufacturerCode`` is the common one (many Tuya / Bosch attributes are
manufacturer-specific and are rejected without it).

Like :mod:`~cli_anything.zigbee2mqtt.core.scenes` these are *cluster commands*,
not bridge requests: there is no ``bridge/response`` topic, so the helpers
return the published payload plus the publish rc. The answer to a read comes
back asynchronously on the device's own state topic — read it with
``device state`` (retained) or watch it live with ``device watch``.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

# The check_* helpers are public on purpose: the CLI calls them to reject bad
# arguments *before* opening an MQTT connection, so a typo fails with a clear
# message even when no broker is reachable.


def check_target(target: str) -> str:
    if not target or not str(target).strip():
        raise ValueError("target is required (device friendly_name or ieee_address)")
    return str(target).strip()


def check_cluster(cluster: Any) -> Any:
    """Validate a cluster identifier.

    z2m accepts either a herdsman cluster *name* (``"genBasic"``,
    ``"msTemperatureMeasurement"``) or a numeric cluster id (``0``, ``6``).
    Numeric ids are passed through as ints so the JSON payload matches what
    zigbee-herdsman expects.
    """
    if isinstance(cluster, bool):
        raise ValueError("cluster must be a name or numeric id")
    if isinstance(cluster, int):
        return cluster
    if cluster is None or not str(cluster).strip():
        raise ValueError("cluster is required (e.g. genBasic, or a numeric cluster id)")
    text = str(cluster).strip()
    try:
        return int(text, 0)
    except ValueError:
        return text


def check_attributes(attributes: Iterable[Any]) -> list[Any]:
    """Normalise the attribute list for a read (names or numeric ids)."""
    if attributes is None:
        raise ValueError("at least one attribute is required")
    out: list[Any] = []
    for attr in attributes:
        if attr is None or (isinstance(attr, str) and not attr.strip()):
            raise ValueError("attribute names must be non-empty")
        if isinstance(attr, str):
            text = attr.strip()
            try:
                out.append(int(text, 0))
            except ValueError:
                out.append(text)
        else:
            out.append(attr)
    if not out:
        raise ValueError("at least one attribute is required")
    return out


def check_options(
    options: Optional[dict] = None, *, manufacturer_code: Optional[int] = None
) -> dict:
    """Merge ``--option k=v`` pairs with the ``--manufacturer-code`` shortcut."""
    merged: dict = {}
    if options:
        if not isinstance(options, dict):
            raise ValueError("options must be a dict")
        merged.update(options)
    if manufacturer_code is not None:
        merged["manufacturerCode"] = int(manufacturer_code)
    return merged


def set_topic(client: BridgeClient, target: str, *, endpoint: Optional[int | str] = None) -> str:
    """Return the ``/set`` topic for *target*, optionally endpoint-scoped.

    Attributes live per endpoint, so a multi-endpoint device needs
    ``<base>/<name>/<endpoint>/set`` to reach the right one.
    """
    target = check_target(target)
    if endpoint is None or endpoint == "":
        return f"{client.base_topic}/{target}/set"
    return f"{client.base_topic}/{target}/{endpoint}/set"


def _publish(
    client: BridgeClient,
    target: str,
    payload: dict,
    *,
    endpoint: Optional[int | str] = None,
) -> dict:
    topic = set_topic(client, target, endpoint=endpoint)
    rc = client.publish(topic, payload)
    return {"target": check_target(target), "topic": topic, "published": payload, "rc": rc}


# ── read / write ────────────────────────────────────────────────────────


def read(
    client: BridgeClient,
    target: str,
    cluster: Any,
    attributes: Iterable[Any],
    *,
    endpoint: Optional[int | str] = None,
    options: Optional[dict] = None,
    manufacturer_code: Optional[int] = None,
) -> dict:
    """Issue a ZCL *read attributes* for *cluster* on *target*.

    The device's answer is published asynchronously on its state topic, so
    follow this with ``device state <target>`` (retained) or run
    ``device watch <target>`` alongside it to capture the value.
    """
    body: dict[str, Any] = {
        "cluster": check_cluster(cluster),
        "attributes": check_attributes(attributes),
    }
    opts = check_options(options, manufacturer_code=manufacturer_code)
    if opts:
        body["options"] = opts
    return _publish(client, target, {"read": body}, endpoint=endpoint)


def write(
    client: BridgeClient,
    target: str,
    cluster: Any,
    payload: dict,
    *,
    endpoint: Optional[int | str] = None,
    options: Optional[dict] = None,
    manufacturer_code: Optional[int] = None,
) -> dict:
    """Issue a ZCL *write attributes* for *cluster* on *target*.

    *payload* maps attribute name (or numeric id, as a string key) to the value
    to write, e.g. ``{"onOff": 1}`` or ``{"currentLevel": 254}``. Writes are
    fire-and-forget; read the attribute back to confirm it stuck.
    """
    if not isinstance(payload, dict) or not payload:
        raise ValueError("payload must be a non-empty dict of attribute=value")
    body: dict[str, Any] = {
        "cluster": check_cluster(cluster),
        "payload": dict(payload),
    }
    opts = check_options(options, manufacturer_code=manufacturer_code)
    if opts:
        body["options"] = opts
    return _publish(client, target, {"write": body}, endpoint=endpoint)

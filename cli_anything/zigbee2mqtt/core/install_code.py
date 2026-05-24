"""Install-code joining — for devices that demand a pairing key.

Some commercial-grade and certified zigbee devices (Bosch, certain Aqara,
many enterprise sensors) refuse to join unless their install code is
pre-registered on the coordinator. Z2M exposes this on the bridge as::

    bridge/request/install_code/add     {value}
    bridge/request/install_code/remove  {value}

*value* is either:

* the QR-code text printed on the device label (z2m parses out the install
  code automatically), or
* a hex install-code string of the form ``"ieee:install_code"`` (older z2m
  versions accept only this form).

After ``add``, open the network with :func:`permit_join`, put the device in
pairing mode, and z2m will use the pre-registered code to authenticate the
join.
"""

from __future__ import annotations

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def add(client: BridgeClient, value: str, *,
         timeout: float = 10.0) -> dict:
    """Pre-register an install code on the coordinator.

    *value* can be the device's QR-code text or a raw
    ``"ieee:install_code"`` pair (older z2m fallback).
    """
    if not value:
        raise ValueError("value is required (QR text or ieee:install_code)")
    return client.request("install_code/add", payload={"value": value},
                            timeout=timeout)


def remove(client: BridgeClient, value: str, *,
            timeout: float = 10.0) -> dict:
    """Remove a previously-added install code.

    *value* should be the same string that was passed to :func:`add`. Z2M
    matches on the canonical install-code form internally so the QR text
    and the ``ieee:install_code`` pair are interchangeable.
    """
    if not value:
        raise ValueError("value is required")
    return client.request("install_code/remove", payload={"value": value},
                            timeout=timeout)

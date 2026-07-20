"""Regression test: mqtt_client.py must not import `time` or `__future__`.

These were removed as dead imports. Their absence is verified here so a future
developer does not re-add them without a compelling reason.
"""

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MQTT_CLIENT = REPO_ROOT / "cli_anything" / "zigbee2mqtt" / "core" / "mqtt_client.py"


def test_no_future_annotations_import():
    """The `from __future__ import annotations` line must not be present."""
    source = MQTT_CLIENT.read_text()
    lines = source.splitlines()
    assert not any(
        line.strip() == "from __future__ import annotations" for line in lines
    ), (
        "mqtt_client.py must not contain 'from __future__ import annotations' "
        "— it is unused and was removed as dead import."
    )


def test_no_time_import():
    """The `import time` line must not be present."""
    source = MQTT_CLIENT.read_text()
    lines = source.splitlines()
    for line in lines:
        stripped = line.strip()
        assert not (
            stripped.startswith("import time") or stripped.startswith("from time import")
        ), (
            "mqtt_client.py must not contain `import time` — "
            "the `time` module is unused and was removed as dead import."
        )


def test_no_time_used_in_source():
    """The identifier `time` must not be referenced anywhere in the source."""
    source = MQTT_CLIENT.read_text()
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
    assert "time" not in names, (
        "mqtt_client.py must not reference `time` — "
        "the module is unused and was removed as dead import."
    )

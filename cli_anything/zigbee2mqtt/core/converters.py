"""External-converter file management — list / add / remove / show.

z2m 2.x auto-loads any `.js` file in `<data>/external_converters/`. This module
wraps the kubectl primitives so callers don't have to remember the exact
exec/cp/sed dance.
"""

from __future__ import annotations

from typing import Optional

from cli_anything.zigbee2mqtt.core import k8s_backend


def list_converters(target: k8s_backend.K8sTarget) -> list[dict]:
    """Return one row per .js file in the converters dir, with size + mtime."""
    proc = k8s_backend.exec_(target, ["sh", "-c",
        f"ls -la {target.data_path}/external_converters 2>/dev/null | tail -n +2"],
        check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    rows: list[dict] = []
    for line in out.splitlines():
        parts = line.split()
        # mode links user group size month day time/year name
        if len(parts) < 9 or parts[-1] in (".", ".."):
            continue
        name = parts[-1]
        if not name.endswith(".js") and not name.endswith(".bak") and "." in name:
            # still record .bak/.removed.bak so the user can see them
            pass
        rows.append({
            "name": name,
            "size_bytes": parts[4] if len(parts) > 4 else None,
            "modified": " ".join(parts[5:8]) if len(parts) > 7 else None,
        })
    return rows


def show(target: k8s_backend.K8sTarget, name: str) -> str:
    return k8s_backend.read_external_converter(target, name)


def add(target: k8s_backend.K8sTarget, *, name: str, content: str,
        backup: bool = True) -> dict:
    k8s_backend.write_external_converter(target, name, content, backup=backup)
    return {"name": name, "bytes": len(content.encode("utf-8")),
             "backup": backup}


def add_from_file(target: k8s_backend.K8sTarget, *, name: str,
                   local_path: str, backup: bool = True) -> dict:
    with open(local_path, "r", encoding="utf-8") as f:
        content = f.read()
    return add(target, name=name, content=content, backup=backup)


def remove(target: k8s_backend.K8sTarget, name: str, *,
           backup: bool = True) -> dict:
    k8s_backend.remove_external_converter(target, name, backup=backup)
    return {"name": name, "removed": True, "backup": backup}

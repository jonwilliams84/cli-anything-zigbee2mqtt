"""kubectl helpers for z2m deployments (file mgmt + rollout restart).

Bridge-state changes go over MQTT; the kubectl path is for things MQTT can't
do — pushing/removing external converter files from /app/data/external_converters/
and rolling the deployment when a hot-reload isn't sufficient.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess required for validated kubectl calls
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class K8sTarget:
    namespace: str = "zigbee2mqtt"
    deployment: str = "zigbee2mqtt"
    container: str = "zigbee2mqtt"
    data_path: str = "/app/data"


def _kubectl() -> str:
    path = shutil.which("kubectl")
    if not path:
        raise RuntimeError(
            "kubectl not found on PATH. Install it or set `bridge restart` "
            "to be a no-op (MQTT-only) deployments don't need this."
        )
    return path


def _run(args: list[str], *, stdin: Optional[bytes] = None,
          check: bool = True) -> subprocess.CompletedProcess:
    kc = _kubectl()
    proc = subprocess.run(  # nosec B603 - argv built from validated K8sTarget
        [kc, *args], input=stdin, capture_output=True, text=False, check=False,
    )
    if check and proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed (exit {proc.returncode}): {stderr}"
        )
    return proc


def exec_(target: K8sTarget, argv: list[str], *,
          stdin: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    args = [
        "-n", target.namespace, "exec",
        f"deploy/{target.deployment}", "-c", target.container,
    ]
    if stdin is not None:
        args.append("-i")
    args.append("--")
    args.extend(argv)
    payload = stdin.encode("utf-8") if stdin is not None else None
    return _run(args, stdin=payload, check=check)


def restart(target: K8sTarget) -> None:
    """Trigger a rolling restart of the z2m deployment."""
    _run([
        "-n", target.namespace, "rollout", "restart",
        f"deployment/{target.deployment}",
    ], check=True)


def rollout_status(target: K8sTarget, timeout: str = "180s") -> str:
    proc = _run([
        "-n", target.namespace, "rollout", "status",
        f"deployment/{target.deployment}", f"--timeout={timeout}",
    ], check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    return out + err


def list_external_converters(target: K8sTarget) -> list[str]:
    proc = exec_(target, ["sh", "-c",
                           f"ls -1 {target.data_path}/external_converters 2>/dev/null"],
                  check=False)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    return [line.strip() for line in out.splitlines() if line.strip()]


def read_external_converter(target: K8sTarget, name: str) -> str:
    proc = exec_(target, ["cat", f"{target.data_path}/external_converters/{name}"])
    return (proc.stdout or b"").decode("utf-8", errors="replace")


def write_external_converter(target: K8sTarget, name: str, content: str,
                              *, backup: bool = True) -> None:
    """Push a .js file into z2m's external_converters dir."""
    if "/" in name or name.startswith("."):
        raise ValueError("converter name must be a bare filename ending in .js")
    if not name.endswith(".js"):
        name = name + ".js"
    target_path = f"{target.data_path}/external_converters/{name}"
    # ensure parent dir exists; optional backup
    setup = [f"mkdir -p {target.data_path}/external_converters"]
    if backup:
        setup.append(
            f"[ -f {target_path} ] && cp {target_path} {target_path}.$(date +%s).bak || true"
        )
    setup.append(f"cat > {target_path}")
    exec_(target, ["sh", "-c", " && ".join(setup)], stdin=content)


def remove_external_converter(target: K8sTarget, name: str, *,
                                backup: bool = True) -> None:
    if "/" in name or name.startswith("."):
        raise ValueError("converter name must be a bare filename")
    target_path = f"{target.data_path}/external_converters/{name}"
    if backup:
        exec_(target, ["sh", "-c",
                        f"[ -f {target_path} ] && mv {target_path} {target_path}.$(date +%s).removed.bak"],
              check=False)
    else:
        exec_(target, ["rm", "-f", target_path], check=False)

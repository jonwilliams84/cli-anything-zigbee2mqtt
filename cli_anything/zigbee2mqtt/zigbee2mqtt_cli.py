"""cli-anything-zigbee2mqtt — control a running zigbee2mqtt bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from cli_anything.zigbee2mqtt.core import (
    admin,
    bridge as bridge_core,
    converters as converters_core,
    devices as devices_core,
    groups as groups_core,
    k8s_backend,
    ota as ota_core,
    project,
)
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient, MqttError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


# ──────────────────────────────────────────────────────── helpers

def make_client(ctx: click.Context) -> BridgeClient:
    obj = ctx.obj
    host = obj.get("mqtt_host")
    if not host:
        _abort(
            "no MQTT broker configured. Set with:\n"
            "  cli-anything-zigbee2mqtt --mqtt-host 10.32.100.5 config save"
        )
    return BridgeClient(
        host=host,
        port=obj.get("mqtt_port", 1883),
        username=obj.get("mqtt_username"),
        password=obj.get("mqtt_password"),
        base_topic=obj.get("base_topic", "zigbee2mqtt"),
    )


def make_k8s_target(ctx: click.Context) -> k8s_backend.K8sTarget:
    obj = ctx.obj
    return k8s_backend.K8sTarget(
        namespace=obj["k8s_namespace"],
        deployment=obj["k8s_deployment"],
        container=obj["k8s_container"],
        data_path=obj["k8s_data_path"],
    )


def emit(ctx: click.Context, data) -> None:
    if ctx.obj.get("as_json"):
        click.echo(json.dumps(data, indent=2, default=str, sort_keys=True))
        return
    if data is None:
        return
    if isinstance(data, str):
        click.echo(data)
        return
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            _print_table(data)
        else:
            for item in data:
                click.echo(str(item))
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                click.echo(f"{k}: {json.dumps(v, default=str)}")
            else:
                click.echo(f"{k}: {v}")
        return
    click.echo(str(data))


def _print_table(rows: list[dict]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in keys and not str(k).startswith("_"):
                keys.append(k)
    keys = keys[:10]

    def fmt(v):
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:.2f}"
        if isinstance(v, (list, dict)):
            s = json.dumps(v, default=str)
            return s if len(s) <= 40 else s[:37] + "..."
        s = str(v)
        return s if len(s) <= 40 else s[:37] + "..."

    widths = {k: max(len(k), max(len(fmt(r.get(k))) for r in rows)) for k in keys}
    click.echo("  ".join(k.ljust(widths[k]) for k in keys))
    click.echo("  ".join("-" * widths[k] for k in keys))
    for r in rows:
        click.echo("  ".join(fmt(r.get(k)).ljust(widths[k]) for k in keys))


def _abort(message: str) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(1)


# ──────────────────────────────────────────────────────── root

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--mqtt-host", default=None, help="MQTT broker host (e.g. 10.32.100.5)")
@click.option("--mqtt-port", default=None, type=int, help="MQTT broker port (default 1883)")
@click.option("--mqtt-username", default=None)
@click.option("--mqtt-password", default=None)
@click.option("--base-topic", default=None,
              help="z2m base topic (default 'zigbee2mqtt')")
@click.option("--k8s-namespace", default=None)
@click.option("--k8s-deployment", default=None)
@click.option("--k8s-container", default=None)
@click.option("--k8s-data-path", default=None)
@click.option("--config", "config_path", default=None, type=click.Path(),
              help="Connection profile path (default ~/.config/cli-anything-zigbee2mqtt.json)")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit machine-readable JSON output")
@click.pass_context
def cli(ctx, mqtt_host, mqtt_port, mqtt_username, mqtt_password, base_topic,
        k8s_namespace, k8s_deployment, k8s_container, k8s_data_path,
        config_path, as_json):
    """cli-anything-zigbee2mqtt — bridge + device control over MQTT."""
    ctx.ensure_object(dict)
    cfg_path_obj = Path(config_path).expanduser() if config_path else None
    cfg = project.load_config(cfg_path_obj)
    cfg = project.merge_cli_overrides(
        cfg,
        mqtt_host=mqtt_host, mqtt_port=mqtt_port,
        mqtt_username=mqtt_username, mqtt_password=mqtt_password,
        base_topic=base_topic,
        k8s_namespace=k8s_namespace, k8s_deployment=k8s_deployment,
        k8s_container=k8s_container, k8s_data_path=k8s_data_path,
    )
    ctx.obj.update(cfg)
    ctx.obj["as_json"] = as_json
    ctx.obj["config_path"] = cfg_path_obj
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ──────────────────────────────────────────────────────── profile

@cli.group()
def config():
    """Local connection profile (~/.config/cli-anything-zigbee2mqtt.json)."""


@config.command("show")
@click.pass_context
def config_show(ctx):
    safe = {k: v for k, v in ctx.obj.items() if k not in ("config_path", "as_json")}
    if safe.get("mqtt_password"):
        safe["mqtt_password"] = "***"
    emit(ctx, safe)


@config.command("save")
@click.pass_context
def config_save(ctx):
    safe = {k: v for k, v in ctx.obj.items() if k not in ("config_path", "as_json")}
    out = project.save_config(safe, ctx.obj.get("config_path"))
    emit(ctx, {"saved": str(out)})


# ──────────────────────────────────────────────────────── bridge

@cli.group()
def bridge():
    """Bridge-level info, state, restart, options, log/event tails."""


@bridge.command("info")
@click.pass_context
def bridge_info_cmd(ctx):
    """z2m version, coordinator, network params, log level, permit_join state."""
    with make_client(ctx) as c:
        emit(ctx, bridge_core.info(c))


@bridge.command("state")
@click.pass_context
def bridge_state_cmd(ctx):
    """online / offline."""
    with make_client(ctx) as c:
        emit(ctx, {"state": bridge_core.state(c)})


@bridge.command("restart")
@click.option("--via-kubectl", is_flag=True,
              help="Force a kubectl rollout restart instead of asking z2m politely")
@click.pass_context
def bridge_restart_cmd(ctx, via_kubectl):
    """Restart the z2m process. Hard variant via kubectl when MQTT is unhealthy."""
    if via_kubectl:
        target = make_k8s_target(ctx)
        k8s_backend.restart(target)
        emit(ctx, {"restarted_via": "kubectl", "rollout": k8s_backend.rollout_status(target)})
        return
    with make_client(ctx) as c:
        try:
            emit(ctx, bridge_core.restart(c))
        except MqttError as exc:
            _abort(str(exc))


@bridge.command("health")
@click.pass_context
def bridge_health_cmd(ctx):
    with make_client(ctx) as c:
        emit(ctx, bridge_core.health_check(c))


@bridge.command("options-get")
@click.pass_context
def bridge_options_get(ctx):
    with make_client(ctx) as c:
        emit(ctx, bridge_core.options_get(c))


@bridge.command("options-set")
@click.argument("options_json")
@click.pass_context
def bridge_options_set(ctx, options_json):
    """Patch runtime z2m options. Pass a JSON blob, e.g. '{"advanced":{"log_level":"info"}}'."""
    try:
        payload = json.loads(options_json)
    except json.JSONDecodeError as e:
        _abort(f"options_json is not valid JSON: {e}")
        return
    with make_client(ctx) as c:
        emit(ctx, bridge_core.options_set(c, payload))


@bridge.command("watch-events")
@click.option("--duration", default=15.0, type=float,
              help="Seconds to listen (default 15)")
@click.pass_context
def bridge_watch_events(ctx, duration):
    """Tail bridge events — device joined, removed, OTA progress, etc."""
    with make_client(ctx) as c:
        events = bridge_core.watch_events(c, duration=duration)
    emit(ctx, events)


@bridge.command("watch-logging")
@click.option("--duration", default=15.0, type=float)
@click.pass_context
def bridge_watch_logging(ctx, duration):
    """Tail z2m's own log stream via MQTT."""
    with make_client(ctx) as c:
        if ctx.obj.get("as_json"):
            logs = bridge_core.watch_logging(c, duration=duration)
            emit(ctx, logs)
            return
        bridge_core.watch_logging(
            c, duration=duration,
            callback=lambda d: click.echo(
                f"[{d.get('level','?'):<5}] {d.get('message') or d.get('raw','')}"
            ),
        )


# ──────────────────────────────────────────────────────── devices

@cli.group()
def device():
    """Device list / show / rename / remove / interview / configure / set / get."""


@device.command("list")
@click.option("--full", is_flag=True, help="Include the full raw device records")
@click.pass_context
def device_list(ctx, full):
    with make_client(ctx) as c:
        rows = devices_core.list_devices(c)
    if full:
        emit(ctx, rows)
        return
    emit(ctx, devices_core.summarize(rows))


@device.command("show")
@click.argument("ident")
@click.pass_context
def device_show(ctx, ident):
    """Show full record for one device (IEEE address or friendly_name)."""
    with make_client(ctx) as c:
        d = devices_core.show(c, ident)
    if not d:
        _abort(f"no device matching {ident!r}")
    emit(ctx, d)


@device.command("rename")
@click.argument("from_name")
@click.argument("to_name")
@click.option("--no-ha-rename", is_flag=True,
              help="Don't also rename the HA entities (default: do).")
@click.pass_context
def device_rename(ctx, from_name, to_name, no_ha_rename):
    with make_client(ctx) as c:
        try:
            emit(ctx, devices_core.rename(c, from_=from_name, to=to_name,
                                           homeassistant_rename=not no_ha_rename))
        except MqttError as exc:
            _abort(str(exc))


@device.command("remove")
@click.argument("id_or_name")
@click.option("--force", is_flag=True,
              help="Skip network-level removal (device already physically gone).")
@click.option("--block", is_flag=True,
              help="Add the device to the block-list so it can't rejoin.")
@click.confirmation_option(prompt="Really remove this device from the network?")
@click.pass_context
def device_remove(ctx, id_or_name, force, block):
    with make_client(ctx) as c:
        emit(ctx, devices_core.remove(c, id_or_name, force=force, block=block))


@device.command("configure")
@click.argument("id_or_name")
@click.pass_context
def device_configure(ctx, id_or_name):
    with make_client(ctx) as c:
        emit(ctx, devices_core.configure(c, id_or_name))


@device.command("interview")
@click.argument("id_or_name")
@click.pass_context
def device_interview(ctx, id_or_name):
    with make_client(ctx) as c:
        emit(ctx, devices_core.interview(c, id_or_name))


@device.command("options")
@click.argument("id_or_name")
@click.argument("options_json")
@click.pass_context
def device_options(ctx, id_or_name, options_json):
    """Patch per-device options. e.g. '{"debounce":1, "debounce_ignore":["last_seen"]}'."""
    try:
        payload = json.loads(options_json)
    except json.JSONDecodeError as e:
        _abort(f"options_json is not valid JSON: {e}")
        return
    with make_client(ctx) as c:
        emit(ctx, devices_core.options(c, id_or_name, payload))


@device.command("set")
@click.argument("friendly_name")
@click.argument("fields", nargs=-1)
@click.pass_context
def device_set(ctx, friendly_name, fields):
    """Publish to <base>/<friendly_name>/set. Pass key=value pairs.

    Example: device set 'Lounge Lamp' state=ON brightness=128 color_temp=370
    """
    payload: dict = {}
    for f in fields:
        if "=" not in f:
            _abort(f"expected key=value, got {f!r}")
        k, v = f.split("=", 1)
        try:
            payload[k.strip()] = json.loads(v)
        except json.JSONDecodeError:
            payload[k.strip()] = v
    if not payload:
        _abort("no fields supplied")
    with make_client(ctx) as c:
        rc = devices_core.set_value(c, friendly_name, payload)
    emit(ctx, {"friendly_name": friendly_name, "published": payload, "rc": rc})


@device.command("get")
@click.argument("friendly_name")
@click.argument("keys", nargs=-1)
@click.pass_context
def device_get(ctx, friendly_name, keys):
    """Ask the device to publish state for the listed keys."""
    if not keys:
        _abort("provide at least one key (e.g. state, brightness)")
    with make_client(ctx) as c:
        rc = devices_core.get_value(c, friendly_name, list(keys))
    emit(ctx, {"friendly_name": friendly_name, "asked_for": list(keys), "rc": rc})


@device.command("watch")
@click.argument("friendly_name")
@click.option("--duration", default=10.0, type=float)
@click.pass_context
def device_watch(ctx, friendly_name, duration):
    with make_client(ctx) as c:
        emit(ctx, devices_core.watch_device(c, friendly_name, duration=duration))


# ──────────────────────────────────────────────────────── groups

@cli.group()
def group():
    """Zigbee groups — list / add / remove / membership."""


@group.command("list")
@click.pass_context
def group_list(ctx):
    with make_client(ctx) as c:
        emit(ctx, groups_core.list_groups(c))


@group.command("add")
@click.argument("friendly_name")
@click.option("--id", "id_", type=int, default=None,
              help="Specific group id (default: auto-assign)")
@click.pass_context
def group_add(ctx, friendly_name, id_):
    with make_client(ctx) as c:
        emit(ctx, groups_core.add(c, friendly_name, id_=id_))


@group.command("remove")
@click.argument("id_or_name")
@click.option("--force", is_flag=True)
@click.confirmation_option(prompt="Remove this group?")
@click.pass_context
def group_remove(ctx, id_or_name, force):
    with make_client(ctx) as c:
        emit(ctx, groups_core.remove(c, id_or_name, force=force))


@group.command("rename")
@click.argument("from_name")
@click.argument("to_name")
@click.pass_context
def group_rename(ctx, from_name, to_name):
    with make_client(ctx) as c:
        emit(ctx, groups_core.rename(c, from_name, to_name))


@group.command("add-member")
@click.argument("group_name")
@click.argument("device_name")
@click.pass_context
def group_add_member(ctx, group_name, device_name):
    with make_client(ctx) as c:
        emit(ctx, groups_core.add_member(c, group_name, device_name))


@group.command("remove-member")
@click.argument("group_name")
@click.argument("device_name")
@click.option("--skip-disable-reporting", is_flag=True)
@click.pass_context
def group_remove_member(ctx, group_name, device_name, skip_disable_reporting):
    with make_client(ctx) as c:
        emit(ctx, groups_core.remove_member(
            c, group_name, device_name,
            skip_disable_reporting=skip_disable_reporting,
        ))


@group.command("remove-all")
@click.argument("group_name")
@click.confirmation_option(prompt="Remove ALL members from the group?")
@click.pass_context
def group_remove_all(ctx, group_name):
    with make_client(ctx) as c:
        emit(ctx, groups_core.remove_all_members(c, group_name))


# ──────────────────────────────────────────────────────── ota

@cli.group()
def ota():
    """OTA firmware update."""


@ota.command("check")
@click.argument("id_or_name")
@click.pass_context
def ota_check(ctx, id_or_name):
    with make_client(ctx) as c:
        emit(ctx, ota_core.check(c, id_or_name))


@ota.command("update")
@click.argument("id_or_name")
@click.option("--timeout", default=600.0, type=float,
              help="How long to wait for completion (default 600s).")
@click.confirmation_option(prompt="Trigger OTA update? Device may be unavailable for several minutes.")
@click.pass_context
def ota_update(ctx, id_or_name, timeout):
    with make_client(ctx) as c:
        emit(ctx, ota_core.update(c, id_or_name, timeout=timeout))


@ota.command("schedule")
@click.argument("id_or_name")
@click.pass_context
def ota_schedule(ctx, id_or_name):
    with make_client(ctx) as c:
        emit(ctx, ota_core.schedule(c, id_or_name))


# ──────────────────────────────────────────────────────── network admin

@cli.group("network")
def network_grp():
    """Permit-join, network map, touchlink, coordinator check, backup."""


@network_grp.command("permit-join")
@click.argument("state", type=click.Choice(["on", "off"]))
@click.option("--time", "time_secs", default=254, type=int,
              help="Seconds permit_join stays open (max 254).")
@click.option("--device", default=None,
              help="Open via a specific router (friendly name).")
@click.pass_context
def network_permit_join(ctx, state, time_secs, device):
    with make_client(ctx) as c:
        emit(ctx, admin.permit_join(
            c, value=(state == "on"), time_secs=time_secs, device=device,
        ))


@network_grp.command("map")
@click.option("--type", "type_", default="raw",
              type=click.Choice(["raw", "graphviz", "plantuml"]))
@click.option("--no-routes", is_flag=True, help="Skip route enumeration (faster).")
@click.pass_context
def network_map_cmd(ctx, type_, no_routes):
    """Generate a fresh network map (30-60s)."""
    with make_client(ctx) as c:
        emit(ctx, admin.network_map(c, type_=type_, routes=not no_routes))


@network_grp.command("touchlink-scan")
@click.pass_context
def network_touchlink_scan(ctx):
    with make_client(ctx) as c:
        emit(ctx, admin.touchlink_scan(c))


@network_grp.command("touchlink-identify")
@click.argument("ieee")
@click.argument("channel", type=int)
@click.pass_context
def network_touchlink_identify(ctx, ieee, channel):
    with make_client(ctx) as c:
        emit(ctx, admin.touchlink_identify(c, ieee, channel))


@network_grp.command("touchlink-reset")
@click.option("--ieee", default=None)
@click.option("--channel", default=None, type=int)
@click.confirmation_option(prompt="Factory-reset via touchlink?")
@click.pass_context
def network_touchlink_reset(ctx, ieee, channel):
    with make_client(ctx) as c:
        emit(ctx, admin.touchlink_factory_reset(c, ieee=ieee, channel=channel))


@network_grp.command("coordinator-check")
@click.pass_context
def network_coordinator_check(ctx):
    with make_client(ctx) as c:
        emit(ctx, admin.coordinator_check(c))


@network_grp.command("backup")
@click.pass_context
def network_backup(ctx):
    with make_client(ctx) as c:
        emit(ctx, admin.backup(c))


# ──────────────────────────────────────────────────────── converters

@cli.group()
def converter():
    """Manage z2m's external_converters/ (.js files via kubectl exec)."""


@converter.command("list")
@click.pass_context
def converter_list(ctx):
    target = make_k8s_target(ctx)
    emit(ctx, converters_core.list_converters(target))


@converter.command("show")
@click.argument("name")
@click.pass_context
def converter_show(ctx, name):
    target = make_k8s_target(ctx)
    click.echo(converters_core.show(target, name))


@converter.command("add")
@click.argument("name")
@click.argument("local_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-backup", is_flag=True)
@click.pass_context
def converter_add(ctx, name, local_path, no_backup):
    """Upload a local .js file as a z2m external converter."""
    target = make_k8s_target(ctx)
    emit(ctx, converters_core.add_from_file(
        target, name=name, local_path=local_path, backup=not no_backup,
    ))


@converter.command("remove")
@click.argument("name")
@click.option("--no-backup", is_flag=True)
@click.confirmation_option(prompt="Remove this external converter file?")
@click.pass_context
def converter_remove(ctx, name, no_backup):
    target = make_k8s_target(ctx)
    emit(ctx, converters_core.remove(target, name, backup=not no_backup))


# ──────────────────────────────────────────────────────── REPL

@cli.command()
@click.pass_context
def repl(ctx):
    """Start an interactive shell."""
    try:
        from cli_anything.zigbee2mqtt.utils.repl_skin import ReplSkin
    except ImportError:
        click.echo("REPL requires prompt-toolkit. pip install prompt-toolkit", err=True)
        return
    skin = ReplSkin("zigbee2mqtt", version="0.1.0")
    skin.print_banner()
    pt_session = skin.create_prompt_session()
    while True:
        try:
            line = skin.get_input(pt_session)
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break
        line = (line or "").strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            skin.print_goodbye()
            break
        if line == "help":
            skin.help(cli.commands)
            continue
        import shlex
        argv = shlex.split(line)
        try:
            cli.main(args=argv, standalone_mode=False, prog_name="(z2m)")
        except SystemExit:
            pass
        except Exception as exc:
            skin.error(str(exc))


def main():
    cli(obj={})


if __name__ == "__main__":
    main()

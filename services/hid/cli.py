"""CLI for the AULA F75 Max HID service.

Commands:
    status   -- enumerate and classify the keyboard's HID endpoints
    battery  -- read battery percent over the 2.4G dongle
    light    -- set a lighting mode (wired or dongle transport)
    remap    -- key remapping: apply <file.json> / reset / show <file.json>
                (wired only; `show` never touches hardware)

`--dry-run` prints the exact wire payloads without opening any device.
Dependency injection (enumerator / transport factory) keeps every code path
testable against fakes; only a real invocation without --dry-run constructs
HidapiTransport.
"""

from __future__ import annotations

import argparse
import sys

from . import device, protocol, remap as remap_model
from .client import Client, Transport
from .protocol import LightingConfig, ProtocolError


def _hex(payload: bytes) -> str:
    return payload.hex(" ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aula-hid",
        description="AULA F75 Max stock-firmware HID client",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print wire payloads instead of touching any device",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="enumerate and classify HID endpoints")

    battery = sub.add_parser("battery", help="read battery percent (dongle)")
    battery.add_argument(
        "--timeout", type=float, default=1.25, help="response deadline seconds"
    )

    light = sub.add_parser("light", help="set lighting mode")
    light.add_argument(
        "--mode",
        required=True,
        help=f"0..19 or a name ({', '.join(protocol.LIGHT_MODES.values())})",
    )
    light.add_argument("--brightness", type=int, default=5, help="0..5")
    light.add_argument("--speed", type=int, default=3, help="0..5")
    light.add_argument("--color", default="FFFFFF", help="RRGGBB hex")
    light.add_argument(
        "--direction", type=int, default=0, choices=(0, 1), help="0 or 1"
    )
    light.add_argument(
        "--colorful",
        action="store_true",
        help="rainbow palette instead of the fixed --color",
    )
    light.add_argument(
        "--transport",
        choices=("auto", "wired", "dongle"),
        default="auto",
        help="config channel (auto prefers wired)",
    )

    remap = sub.add_parser(
        "remap",
        help="key remapping (wired feature transaction, normal layer)",
    )
    remap_sub = remap.add_subparsers(dest="remap_command", required=True)
    remap_apply = remap_sub.add_parser(
        "apply", help="resolve a mapping file and send it to the keyboard"
    )
    remap_apply.add_argument("file", help="JSON mapping file (position/send)")
    remap_sub.add_parser(
        "reset", help="clear all remaps (all-zero table = firmware defaults)"
    )
    remap_show = remap_sub.add_parser(
        "show",
        help="print resolved slots and transaction hex; never touches hardware",
    )
    remap_show.add_argument("file", help="JSON mapping file (position/send)")
    return parser


def _discover(enumerator) -> device.Discovery:
    return device.discover(enumerator())


def _cmd_status(args, out, enumerator) -> int:
    found = _discover(enumerator)
    rows = (
        ("wired config (0xFF13 feature)", found.wired_config),
        ("wired screen (0xFF68 output)", found.wired_screen),
        ("dongle config (0xFF60 output)", found.dongle_config),
    )
    any_found = False
    for label, endpoint in rows:
        if endpoint is None:
            print(f"{label}: not present", file=out)
            continue
        any_found = True
        release = (
            f"0x{endpoint.release_number:04X}"
            if endpoint.release_number >= 0
            else "unknown"
        )
        exact = " exact=yes" if endpoint.is_wired and endpoint.is_exact_wired_target else ""
        print(
            f"{label}: {endpoint.vendor_id:04X}:{endpoint.product_id:04X}"
            f" MI_{endpoint.interface_number:02d} product={endpoint.product_string!r}"
            f" release={release}{exact} path={endpoint.path}",
            file=out,
        )
    if not any_found:
        print("no AULA F75 Max endpoints found", file=out)
        return 1
    return 0


def _cmd_battery(args, out, enumerator, transport_factory) -> int:
    if args.dry_run:
        print(f"dongle <- {_hex(protocol.build_battery_request())}", file=out)
        return 0
    found = _discover(enumerator)
    if found.dongle_config is None:
        print("battery requires the 2.4G dongle (05AC:024F) endpoint", file=out)
        return 1
    transport = transport_factory(found.dongle_config)
    client = Client(transport)
    try:
        percent = client.read_battery(timeout_s=args.timeout)
    except TimeoutError as exc:
        print(f"error: {exc}", file=out)
        return 1
    finally:
        client.close()
    print(f"Battery: {percent}%", file=out)
    return 0


def _lighting_config(args) -> LightingConfig:
    red, green, blue = protocol.parse_color(args.color)
    return LightingConfig(
        mode=protocol.resolve_mode(args.mode),
        red=red,
        green=green,
        blue=blue,
        brightness=args.brightness,
        speed=args.speed,
        direction=args.direction,
        colorful=args.colorful,
    ).validate()


def _allow_exact_wired_mutation(endpoint, out, operation: str) -> bool:
    """Fail closed before a wired write to a VID/PID shared by other boards."""
    if endpoint.is_exact_wired_target:
        return True
    release = (
        f"0x{endpoint.release_number:04X}"
        if endpoint.release_number >= 0
        else "unknown"
    )
    print(
        f"refusing {operation}: 0C45:800A is not sufficient identity; "
        f"expected product {device.WIRED_PRODUCT_STRING!r} release "
        f"0x{device.WIRED_RELEASE_NUMBER:04X}, got "
        f"{endpoint.product_string!r} release {release}",
        file=out,
    )
    return False


def _cmd_light(args, out, enumerator, transport_factory) -> int:
    config = _lighting_config(args)

    if args.dry_run:
        if args.transport in ("auto", "wired"):
            print(f"wired <- {_hex(protocol.build_wired_begin())}", file=out)
            print(f"wired <- {_hex(protocol.build_wired_lighting_init())}", file=out)
            print(f"wired <- {_hex(protocol.build_wired_lighting_data(config))}", file=out)
            print(f"wired <- {_hex(protocol.build_wired_apply())}", file=out)
            print(f"wired <- {_hex(protocol.build_wired_finalize())}", file=out)
        if args.transport in ("auto", "dongle"):
            print(f"dongle <- {_hex(protocol.build_dongle_lighting(config))}", file=out)
        return 0

    found = _discover(enumerator)
    endpoint = None
    if args.transport == "wired":
        endpoint = found.wired_config
    elif args.transport == "dongle":
        endpoint = found.dongle_config
    else:
        endpoint = found.preferred_config
    if endpoint is None:
        print(f"no {args.transport} config endpoint found", file=out)
        return 1
    if (
        endpoint.kind == device.KIND_WIRED_CONFIG
        and not _allow_exact_wired_mutation(endpoint, out, "wired lighting")
    ):
        return 1

    transport = transport_factory(endpoint)
    client = Client(transport)
    try:
        if endpoint.kind == device.KIND_WIRED_CONFIG:
            client.set_lighting_wired(config)
        else:
            client.set_lighting_dongle(config)
    finally:
        client.close()
    mode_name = protocol.LIGHT_MODES[config.mode]
    print(
        f"Lighting set: {mode_name} (mode {config.mode}) via {endpoint.kind}",
        file=out,
    )
    return 0


def _resolved_remaps(args):
    """Resolve (remaps, layout) for the remap subcommands. `reset` carries no
    file and resolves the identity mapping (all slots zero)."""
    layout = remap_model.load_layout()
    if args.remap_command == "reset":
        entries = remap_model.default_mapping()
    else:
        entries = remap_model.load_mapping_file(args.file)
    return remap_model.resolve_mapping(entries, layout), layout


def _print_remap_plan(remaps, layout, out) -> None:
    """Stable dry-run/show output: resolved slots, then the wire sequence."""
    for entry in remaps:
        print(f"slot: {remap_model.describe_remap(entry, layout)}", file=out)
    if not remaps:
        print("slot: none (identity table, clears all remaps)", file=out)
    for label, payload in remap_model.remap_transactions(remaps):
        print(f"wired <- [{label}] {_hex(payload)}", file=out)


def _cmd_remap(args, out, enumerator, transport_factory) -> int:
    remaps, layout = _resolved_remaps(args)

    if args.remap_command == "show" or args.dry_run:
        _print_remap_plan(remaps, layout, out)
        return 0

    found = _discover(enumerator)
    if found.wired_config is None:
        # Remap is a wired feature transaction; the vendor app's wireless
        # remap sender is a different, undocumented path (contract 6.4).
        print("remap requires the wired (0C45:800A) config endpoint", file=out)
        return 1
    if not _allow_exact_wired_mutation(found.wired_config, out, "remap"):
        return 1

    transport = transport_factory(found.wired_config)
    client = Client(transport)
    try:
        client.apply_remap(remaps)
    finally:
        client.close()
    if args.remap_command == "reset":
        print("Remap reset: all keys back to firmware defaults", file=out)
    else:
        print(f"Remap applied: {len(remaps)} keys from {args.file}", file=out)
    return 0


def _default_transport_factory(endpoint: device.Endpoint) -> Transport:
    from .client import HidapiTransport  # noqa: PLC0415 (lazy: hardware path)

    return HidapiTransport(endpoint)


def main(
    argv: list[str] | None = None,
    out=None,
    enumerator=None,
    transport_factory=None,
) -> int:
    out = out if out is not None else sys.stdout
    enumerator = enumerator if enumerator is not None else device.enumerate_hid
    transport_factory = (
        transport_factory
        if transport_factory is not None
        else _default_transport_factory
    )

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            return _cmd_status(args, out, enumerator)
        if args.command == "battery":
            return _cmd_battery(args, out, enumerator, transport_factory)
        if args.command == "light":
            return _cmd_light(args, out, enumerator, transport_factory)
        if args.command == "remap":
            return _cmd_remap(args, out, enumerator, transport_factory)
    except ProtocolError as exc:
        print(f"error: {exc}", file=out)
        return 2
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

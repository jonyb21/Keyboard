"""CLI for the AULA F75 Max HID service.

Commands:
    status   -- enumerate and classify the keyboard's HID endpoints
    battery  -- read battery percent over the 2.4G dongle
    light    -- set a lighting mode (wired or dongle transport)
               or apply/show a named stock/per-key profile
    clock    -- synchronize the wired LCD clock
    screen   -- inspect, prepare, upload, restore, or test the 128x128 LCD
    remap    -- key remapping: apply <file.json> / reset / show <file.json>
                (wired only; `show` never touches hardware)

`--dry-run` prints deterministic operation evidence without opening any device.
Verified feature-report commands include exact wire hex. Screen commands print
the prepared media hash, geometry, and page plan; capture-gated per-key output
prints the compiled table and refusal reason because no safe wire transaction
exists yet.
Dependency injection (enumerator / transport factory) keeps every code path
testable against fakes; only a real invocation without --dry-run constructs
HidapiTransport.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from . import custom_lighting, device, protocol, remap as remap_model, screen
from .client import AckError, Client, ScreenClient, ScreenUploadError, Transport
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
        help="print deterministic operation evidence without touching any device",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="enumerate and classify HID endpoints")

    battery = sub.add_parser("battery", help="read battery percent (dongle)")
    battery.add_argument(
        "--timeout", type=float, default=1.25, help="response deadline seconds"
    )

    light = sub.add_parser("light", help="set lighting mode")
    light.add_argument(
        "light_action",
        nargs="?",
        choices=("preset", "custom"),
        help="preset applies a safe stock-engine profile; custom shows per-key data",
    )
    light.add_argument(
        "profile",
        nargs="?",
        help="named profile: Focus Core or Aurora",
    )
    light.add_argument(
        "--mode",
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
    light.add_argument(
        "--show",
        action="store_true",
        help="show compiled per-key slots; never touches hardware",
    )

    clock = sub.add_parser("clock", help="wired LCD clock")
    clock_sub = clock.add_subparsers(dest="clock_command", required=True)
    clock_sub.add_parser("sync", help="synchronize the LCD clock to local time")

    lcd = sub.add_parser("screen", help="128x128 LCD image tools")
    lcd_sub = lcd.add_subparsers(dest="screen_command", required=True)
    for command, help_text in (
        ("inspect", "decode and print image transfer metadata"),
        ("prepare", "write a page-aligned local transfer file"),
        ("upload", "decode and upload an image to the exact F75 Max"),
    ):
        item = lcd_sub.add_parser(command, help=help_text)
        item.add_argument("source", help="PNG/JPEG/GIF/BMP/TIFF/WebP source")
        item.add_argument(
            "--fit", choices=tuple(sorted(screen.FIT_MODES)), default="contain"
        )
        item.add_argument("--max-frames", type=int, default=screen.MAX_FRAMES)
        if command == "prepare":
            item.add_argument("--output", help="destination .f75lcd stream")
    restore = lcd_sub.add_parser(
        "restore", help="upload the preserved vendor PNG frame sequence"
    )
    restore.add_argument(
        "--source",
        help="numbered PNG directory or explicit GIF override",
    )
    restore.add_argument(
        "--fit", choices=tuple(sorted(screen.FIT_MODES)), default="stretch"
    )
    restore.add_argument("--max-frames", type=int, default=screen.MAX_FRAMES)
    restore.add_argument(
        "--delay",
        type=int,
        help="explicit candidate wire delay byte for a PNG sequence (1..255)",
    )
    restore.add_argument(
        "--allow-unverified-timing",
        action="store_true",
        help="allow live restore with timing that has not been visually verified",
    )
    lcd_sub.add_parser(
        "test-pattern", help="upload a generated alignment/primary-color pattern"
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
    if args.dry_run:
        raise ProtocolError(
            "--dry-run status is invalid; status is already read-only and must enumerate"
        )
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
    endpoint = device.require_exact_dongle_config(found)
    transport = transport_factory(endpoint)
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
    if args.mode is None:
        raise ProtocolError("light --mode is required unless using preset/custom")
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
    if args.show and args.light_action != "custom":
        raise ProtocolError("--show is valid only with light custom")
    if args.light_action == "custom":
        if not args.profile:
            raise ProtocolError("light custom requires a profile name")
        compiled = custom_lighting.compile_profile(args.profile)
        print(
            f"Per-key profile: {compiled.name} keys={len(compiled.keys)} "
            f"table_sha256={compiled.table_sha256}",
            file=out,
        )
        for key in compiled.keys:
            print(
                f"light_index={key.light_index:03d} key={key.name} color={key.color_hex}",
                file=out,
            )
        print(custom_lighting.CAPTURE_REQUIRED, file=out)
        if args.show or args.dry_run:
            return 0
        custom_lighting.require_per_key_capture()

    if args.light_action == "preset":
        if not args.profile:
            raise ProtocolError("light preset requires a profile name")
        compiled = custom_lighting.compile_profile(args.profile)
        config = compiled.stock
        profile_label = compiled.name
    else:
        if args.profile is not None:
            raise ProtocolError("unexpected profile; use light preset or light custom")
        config = _lighting_config(args)
        profile_label = None

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
        if found.wired_config is not None:
            if not _allow_exact_wired_mutation(
                found.wired_config, out, "wired lighting"
            ):
                return 1
            endpoint = device.require_exact_wired_config(found)
    elif args.transport == "dongle":
        if found.dongle_config is not None:
            endpoint = device.require_exact_dongle_config(found)
    else:
        if found.wired_config is not None:
            if not _allow_exact_wired_mutation(
                found.wired_config, out, "wired lighting"
            ):
                return 1
            endpoint = device.require_exact_wired_config(found)
        elif found.dongle_config is not None:
            endpoint = device.require_exact_dongle_config(found)
    if endpoint is None:
        print(f"no {args.transport} config endpoint found", file=out)
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
        f"Lighting set: "
        f"{profile_label + ' -> ' if profile_label else ''}"
        f"{mode_name} (mode {config.mode}) via {endpoint.kind}",
        file=out,
    )
    return 0


def _clock_packets(when: time.struct_time) -> tuple[bytes, ...]:
    weekday = (when.tm_wday + 1) % 7
    return (
        protocol.build_wired_begin(),
        protocol.build_wired_clock_init(),
        protocol.build_wired_clock_data(
            when.tm_year,
            when.tm_mon,
            when.tm_mday,
            when.tm_hour,
            when.tm_min,
            when.tm_sec,
            weekday,
        ),
        protocol.build_wired_apply(),
    )


def _cmd_clock(args, out, enumerator, transport_factory) -> int:
    when = time.localtime()
    if args.dry_run:
        for packet in _clock_packets(when):
            print(f"wired <- {_hex(packet)}", file=out)
        return 0

    found = _discover(enumerator)
    if found.wired_config is None:
        print("clock sync requires the wired (0C45:800A) config endpoint", file=out)
        return 1
    endpoint = device.require_exact_wired_config(found)
    client = Client(transport_factory(endpoint))
    try:
        client.sync_clock_wired(when)
    finally:
        client.close()
    print(time.strftime("Clock synchronized: %Y-%m-%d %H:%M:%S", when), file=out)
    return 0


REPO_ROOT = Path(__file__).resolve().parents[2]
LCD_ASSET_MANIFEST = REPO_ROOT / "services" / "hid" / "data" / "lcd_assets.json"


def _manifest_path(root: Path, text: object, field: str) -> Path:
    if not isinstance(text, str) or not text.strip():
        raise screen.ScreenError(f"LCD restore manifest {field} must be text")
    path = (root / text).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise screen.ScreenError(
            f"LCD restore manifest {field} escapes the repository"
        ) from exc
    return path


def _default_restore_sequence(manifest: dict) -> Path:
    restore = manifest["restore"]
    local = _manifest_path(REPO_ROOT, restore.get("local_directory"), "local_directory")
    fallback = _manifest_path(
        REPO_ROOT, restore.get("fallback_directory"), "fallback_directory"
    )
    if local.is_dir():
        return local
    if fallback.is_dir():
        return fallback
    raise screen.ScreenError(
        "default restore frames are missing from both manifest locations"
    )


def _prepare_screen_command(args) -> screen.PreparedScreen:
    if args.screen_command == "test-pattern":
        return screen.prepare_test_pattern()
    if args.screen_command == "restore":
        manifest = None
        if args.source:
            source = Path(args.source).resolve()
        else:
            manifest = screen.load_lcd_manifest(LCD_ASSET_MANIFEST)
            source = _default_restore_sequence(manifest)
        if source.is_dir():
            if args.delay is None:
                raise screen.ScreenError(
                    "PNG-sequence restore requires an explicit --delay candidate"
                )
            prepared = screen.prepare_frame_sequence(
                source,
                delay=args.delay,
                fit=args.fit,
                max_frames=args.max_frames,
            )
            if manifest is not None:
                screen.validate_restore_manifest(prepared, manifest, args.delay)
            return prepared
        if source.suffix.lower() != ".gif":
            raise screen.ScreenError(
                "restore --source must be a numbered PNG directory or explicit GIF"
            )
        return screen.prepare_image(source, fit=args.fit, max_frames=args.max_frames)
    source = Path(args.source)
    return screen.prepare_image(source, fit=args.fit, max_frames=args.max_frames)


def _print_screen_metadata(prepared: screen.PreparedScreen, out) -> None:
    print(prepared.metadata_json(), file=out)


def _cmd_screen(args, out, enumerator, transport_factory) -> int:
    if (
        args.screen_command == "restore"
        and args.source
        and Path(args.source).suffix.lower() == ".gif"
    ):
        print(
            "warning: explicit GIF restore uses embedded frame durations; "
            "the installed vendor 0.gif has zero durations and is not the default",
            file=out,
        )
    if (
        args.screen_command == "restore"
        and not args.dry_run
        and not args.allow_unverified_timing
    ):
        raise screen.ScreenError(
            "live restore timing is unverified; pass --allow-unverified-timing "
            "after choosing an explicit candidate --delay"
        )
    prepared = _prepare_screen_command(args)
    _print_screen_metadata(prepared, out)

    if args.screen_command == "inspect":
        return 0
    if args.screen_command == "prepare":
        destination = (
            Path(args.output)
            if args.output
            else REPO_ROOT / ".local-assets" / "lcd" / "prepared"
            / f"{Path(args.source).stem}-{prepared.stream_sha256[:12]}.f75lcd"
        )
        if args.dry_run:
            print(f"dry-run: would write {destination}", file=out)
        else:
            screen.write_prepared(prepared, destination)
            print(f"Prepared stream written: {destination}", file=out)
        return 0

    if args.dry_run:
        print(
            f"dry-run: exact pair required; would upload {prepared.page_count} pages",
            file=out,
        )
        return 0

    allow_ack_learning = args.screen_command == "test-pattern"
    if protocol.SCREEN_ACK_PREFIX is None and not allow_ack_learning:
        raise ScreenUploadError(
            "screen ACK prefix is not pinned; run live screen test-pattern first"
        )

    found = _discover(enumerator)
    client = ScreenClient.open_exact(
        found,
        transport_factory,
        allow_ack_learning=allow_ack_learning,
    )
    try:
        result = client.upload(prepared.stream)
    finally:
        client.close()
    print(
        f"Screen uploaded: {result.page_count} pages "
        f"ack_prefix={result.ack_prefix.hex(' ')} "
        f"ack_count={len(result.page_ack_prefixes)} "
        f"ack_prefixes={','.join(item.hex() for item in result.page_ack_prefixes)} "
        f"stream_sha256={prepared.stream_sha256}",
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
    endpoint = device.require_exact_wired_config(found)
    transport = transport_factory(endpoint)
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
        if args.command == "clock":
            return _cmd_clock(args, out, enumerator, transport_factory)
        if args.command == "screen":
            return _cmd_screen(args, out, enumerator, transport_factory)
        if args.command == "remap":
            return _cmd_remap(args, out, enumerator, transport_factory)
    except (
        AckError,
        device.DeviceSelectionError,
        ProtocolError,
        screen.ScreenError,
        ScreenUploadError,
    ) as exc:
        print(f"error: {exc}", file=out)
        return 2
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

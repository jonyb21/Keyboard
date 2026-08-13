"""Device discovery for the AULA F75 Max.

Classifies HID enumeration records (dicts shaped like hidapi's
`hid.enumerate()` output) into the three endpoints the protocol uses.
Pure classification logic is deterministic and test-covered against fake
enumerations; the only I/O is the optional `enumerate_hid()` helper, which is
never called by tests.

Interface selection contract: contracts/hid_protocol.md section 1.
"""

from __future__ import annotations

from dataclasses import dataclass

# Identity. Exact vendor v1.0.0.5 config.xml, rechecked by device_audit:
# wired "USB" mode 0C45:800A, "2.4G TYPE-A" dongle 05AC:024F.
WIRED_VID = 0x0C45
WIRED_PID = 0x800A
WIRED_PRODUCT_STRING = "AULA F75Max"
WIRED_RELEASE_NUMBER = 0x0108
DONGLE_VID = 0x05AC
DONGLE_PID = 0x024F

# Usage pages. Wired config collection 0xFF13 per
# [OSX] Sources/AulaF75Bar/main.m OpenScreenHID and
# [F108] pkg/aula/transport_windows.go findHIDDevice(..., 0xFF13).
WIRED_CONFIG_USAGE_PAGE = 0xFF13
# Wired screen pipe 0xFF68 per the same two sources.
WIRED_SCREEN_USAGE_PAGE = 0xFF68
# Dongle raw endpoint 0xFF60 usage 0x61 per [OSX] main.m
# (endpoint.usagePage == 0xff60 && endpoint.usage == 0x61).
DONGLE_CONFIG_USAGE_PAGE = 0xFF60
DONGLE_CONFIG_USAGE = 0x61

# Wired interface-number fallbacks, used only when the backend reports no
# usage page (e.g. Linux hidraw). Registry map: MI_02 = 0xFF68 screen and
# MI_03 = 0xFF13 config. The dongle has no fallback because interface MI_03
# alone does not prove the required 0xFF60/0x61 raw collection.
WIRED_CONFIG_INTERFACE = 3
WIRED_SCREEN_INTERFACE = 2

KIND_WIRED_CONFIG = "wired-config"
KIND_WIRED_SCREEN = "wired-screen"
KIND_DONGLE_CONFIG = "dongle-config"


class DeviceSelectionError(ValueError):
    """Discovery did not yield one unambiguous exact hardware target."""


@dataclass(frozen=True)
class Endpoint:
    """One classified HID endpoint."""

    kind: str
    path: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    interface_number: int
    product_string: str
    release_number: int
    serial_number: str = ""
    pair_token: str = ""

    @property
    def is_wired(self) -> bool:
        return self.kind in (KIND_WIRED_CONFIG, KIND_WIRED_SCREEN)

    @property
    def is_exact_wired_target(self) -> bool:
        """True only for Jon's model/revision, not another shared-PID board."""
        return (
            self.is_wired
            and self.vendor_id == WIRED_VID
            and self.product_id == WIRED_PID
            and self.product_string == WIRED_PRODUCT_STRING
            and self.release_number == WIRED_RELEASE_NUMBER
        )

    @property
    def is_exact_wired_config(self) -> bool:
        return (
            self.is_exact_wired_target
            and self.kind == KIND_WIRED_CONFIG
            and self.interface_number == WIRED_CONFIG_INTERFACE
            and self.usage_page in (0, WIRED_CONFIG_USAGE_PAGE)
        )

    @property
    def is_exact_wired_screen(self) -> bool:
        return (
            self.is_exact_wired_target
            and self.kind == KIND_WIRED_SCREEN
            and self.interface_number == WIRED_SCREEN_INTERFACE
            and self.usage_page in (0, WIRED_SCREEN_USAGE_PAGE)
        )

    @property
    def is_exact_dongle_target(self) -> bool:
        return (
            self.kind == KIND_DONGLE_CONFIG
            and self.vendor_id == DONGLE_VID
            and self.product_id == DONGLE_PID
            and self.usage_page == DONGLE_CONFIG_USAGE_PAGE
            and self.usage == DONGLE_CONFIG_USAGE
        )


@dataclass(frozen=True)
class Discovery:
    """Classified endpoints for one enumeration pass."""

    wired_config: Endpoint | None
    wired_screen: Endpoint | None
    dongle_config: Endpoint | None
    wired_config_candidates: tuple[Endpoint, ...] = ()
    wired_screen_candidates: tuple[Endpoint, ...] = ()
    dongle_config_candidates: tuple[Endpoint, ...] = ()

    @property
    def preferred_config(self) -> Endpoint | None:
        """Wired config first (vendor app behaviour), dongle second."""
        return self.wired_config or self.dongle_config


def _field(record: dict, name: str, default: int = 0) -> int:
    value = record.get(name, default)
    return default if value is None else int(value)


def _path(record: dict) -> str:
    path = record.get("path", "")
    if isinstance(path, bytes):
        return path.decode("utf-8", "replace")
    return str(path)


def _text_field(record: dict, name: str) -> str:
    value = record.get(name, "")
    return "" if value is None else str(value)


def classify(record: dict) -> str | None:
    """Classify one hidapi enumeration record; None if it is not ours.

    Primary key: usage page (and usage for the dongle). Wired-only fallback
    when the backend reports usage_page 0: interface number.
    """
    vid = _field(record, "vendor_id")
    pid = _field(record, "product_id")
    usage_page = _field(record, "usage_page")
    usage = _field(record, "usage")
    interface = _field(record, "interface_number", -1)

    if (vid, pid) == (WIRED_VID, WIRED_PID):
        if usage_page == WIRED_CONFIG_USAGE_PAGE:
            return KIND_WIRED_CONFIG
        if usage_page == WIRED_SCREEN_USAGE_PAGE:
            return KIND_WIRED_SCREEN
        if usage_page == 0:
            if interface == WIRED_CONFIG_INTERFACE:
                return KIND_WIRED_CONFIG
            if interface == WIRED_SCREEN_INTERFACE:
                return KIND_WIRED_SCREEN
        return None

    if (vid, pid) == (DONGLE_VID, DONGLE_PID):
        if usage_page == DONGLE_CONFIG_USAGE_PAGE and usage == DONGLE_CONFIG_USAGE:
            return KIND_DONGLE_CONFIG
        return None

    return None


def discover(records: list[dict]) -> Discovery:
    """Classify an enumeration list into a Discovery.

    First match per kind wins unless a later record proves the exact F75 Max
    product/revision and the first record did not.
    """
    found: dict[str, Endpoint] = {}
    candidates: dict[str, list[Endpoint]] = {
        KIND_WIRED_CONFIG: [],
        KIND_WIRED_SCREEN: [],
        KIND_DONGLE_CONFIG: [],
    }
    for record in records:
        kind = classify(record)
        if kind is None:
            continue
        candidate = Endpoint(
            kind=kind,
            path=_path(record),
            vendor_id=_field(record, "vendor_id"),
            product_id=_field(record, "product_id"),
            usage_page=_field(record, "usage_page"),
            usage=_field(record, "usage"),
            interface_number=_field(record, "interface_number", -1),
            product_string=_text_field(record, "product_string"),
            release_number=_field(record, "release_number", -1),
            serial_number=_text_field(record, "serial_number"),
            pair_token=_text_field(record, "pair_token"),
        )
        candidates[kind].append(candidate)
        existing = found.get(kind)
        # Shared VID/PID hardware exists. Prefer the exact F75 Max/revision if
        # the enumeration contains both it and a related SONiX device.
        if existing is None or (
            candidate.is_exact_wired_target
            and not existing.is_exact_wired_target
        ):
            found[kind] = candidate
    return Discovery(
        wired_config=found.get(KIND_WIRED_CONFIG),
        wired_screen=found.get(KIND_WIRED_SCREEN),
        dongle_config=found.get(KIND_DONGLE_CONFIG),
        wired_config_candidates=tuple(candidates[KIND_WIRED_CONFIG]),
        wired_screen_candidates=tuple(candidates[KIND_WIRED_SCREEN]),
        dongle_config_candidates=tuple(candidates[KIND_DONGLE_CONFIG]),
    )


def require_exact_screen_pair(discovery: Discovery) -> tuple[Endpoint, Endpoint]:
    """Return the sole exact MI_03/MI_02 pair or fail closed.

    LCD writes use two HID handles.  Selecting the first match is unsafe when
    two shared-PID boards are connected, so the screen path requires exactly
    one classified control candidate and exactly one screen candidate.  A
    backend-provided serial or pair token must also agree across both handles.
    """

    configs = discovery.wired_config_candidates
    screens = discovery.wired_screen_candidates
    # Keep manually constructed Discovery values useful to API consumers.
    if not configs and discovery.wired_config is not None:
        configs = (discovery.wired_config,)
    if not screens and discovery.wired_screen is not None:
        screens = (discovery.wired_screen,)

    if len(configs) != 1 or len(screens) != 1:
        raise DeviceSelectionError(
            "screen upload requires exactly one MI_03 control endpoint and "
            "one MI_02 screen endpoint; found "
            f"{len(configs)} control and {len(screens)} screen"
        )
    control, screen = configs[0], screens[0]
    if not control.is_exact_wired_config or not screen.is_exact_wired_screen:
        raise DeviceSelectionError(
            "screen upload requires exact AULA F75Max 0C45:800A release "
            "0x0108 MI_03 control and MI_02 screen endpoints"
        )
    if (control.serial_number or screen.serial_number) and (
        control.serial_number != screen.serial_number
    ):
        raise DeviceSelectionError("screen endpoints have different serial numbers")
    if (control.pair_token or screen.pair_token) and (
        control.pair_token != screen.pair_token
    ):
        raise DeviceSelectionError("screen endpoints have different pair tokens")
    return control, screen


def require_exact_wired_config(discovery: Discovery) -> Endpoint:
    """Return the sole exact MI_03 wired config endpoint or fail closed."""

    candidates = discovery.wired_config_candidates
    if not candidates and discovery.wired_config is not None:
        candidates = (discovery.wired_config,)
    if len(candidates) != 1:
        raise DeviceSelectionError(
            "operation requires exactly one exact wired MI_03 config endpoint; "
            f"found {len(candidates)}"
        )
    endpoint = candidates[0]
    if not endpoint.is_exact_wired_config:
        raise DeviceSelectionError(
            "operation requires exactly one exact wired AULA F75Max "
            "0C45:800A release 0x0108 MI_03 config endpoint"
        )
    return endpoint


def require_exact_dongle_config(discovery: Discovery) -> Endpoint:
    """Return the sole exact 05AC:024F/FF60:61 dongle endpoint."""

    candidates = discovery.dongle_config_candidates
    if not candidates and discovery.dongle_config is not None:
        candidates = (discovery.dongle_config,)
    if len(candidates) != 1:
        raise DeviceSelectionError(
            "operation requires exactly one exact dongle 05AC:024F "
            f"0xFF60:0x61 endpoint; found {len(candidates)}"
        )
    endpoint = candidates[0]
    if not endpoint.is_exact_dongle_target:
        raise DeviceSelectionError(
            "operation requires exactly one exact dongle 05AC:024F "
            "usage page 0xFF60 usage 0x61 endpoint"
        )
    return endpoint


def enumerate_hid() -> list[dict]:
    """Enumerate HID devices via hidapi. The only I/O in this module.

    Import is deferred so protocol/tests never require the hidapi package.
    """
    import hid  # noqa: PLC0415  (deliberate lazy import, see docstring)

    records = []
    for vid, pid in ((WIRED_VID, WIRED_PID), (DONGLE_VID, DONGLE_PID)):
        records.extend(hid.enumerate(vid, pid))
    return records

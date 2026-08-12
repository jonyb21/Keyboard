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

# Interface-number fallbacks, used only when the backend reports no usage
# page (e.g. Linux hidraw). Wired registry map (docs report section 5):
# MI_02 = 0xFF68 screen, MI_03 = 0xFF13 config. Dongle binding
# VID_05AC&PID_024F&MI_03 from vendor config.xml.
WIRED_CONFIG_INTERFACE = 3
WIRED_SCREEN_INTERFACE = 2
DONGLE_CONFIG_INTERFACE = 3

KIND_WIRED_CONFIG = "wired-config"
KIND_WIRED_SCREEN = "wired-screen"
KIND_DONGLE_CONFIG = "dongle-config"


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

    @property
    def is_wired(self) -> bool:
        return self.kind in (KIND_WIRED_CONFIG, KIND_WIRED_SCREEN)

    @property
    def is_exact_wired_target(self) -> bool:
        """True only for Jon's model/revision, not another shared-PID board."""
        return (
            self.is_wired
            and self.product_string == WIRED_PRODUCT_STRING
            and self.release_number == WIRED_RELEASE_NUMBER
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

    Primary key: usage page (and usage for the dongle). Fallback when the
    backend reports usage_page 0: interface number.
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
        if usage_page == 0 and interface == DONGLE_CONFIG_INTERFACE:
            return KIND_DONGLE_CONFIG
        return None

    return None


def discover(records: list[dict]) -> Discovery:
    """Classify an enumeration list into a Discovery.

    First match per kind wins unless a later record proves the exact F75 Max
    product/revision and the first record did not.
    """
    found: dict[str, Endpoint] = {}
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
        )
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
    )


def enumerate_hid() -> list[dict]:
    """Enumerate HID devices via hidapi. The only I/O in this module.

    Import is deferred so protocol/tests never require the hidapi package.
    """
    import hid  # noqa: PLC0415  (deliberate lazy import, see docstring)

    records = []
    for vid, pid in ((WIRED_VID, WIRED_PID), (DONGLE_VID, DONGLE_PID)):
        records.extend(hid.enumerate(vid, pid))
    return records

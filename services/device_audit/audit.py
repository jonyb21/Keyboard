"""Deterministic, read-only inventory for the exact AULA F75 Max in this repo.

The service reads three independent evidence sources:

* Windows Plug and Play for the physical USB identity and hardware revision.
* Windows uninstall metadata plus the vendor ``config.xml`` for configurator
  identity and the USB target it actually supports.
* The vendor SQLite database for active profile and response-level state.

It never opens a HID endpoint and never changes the device, registry, files, or
database.  All compatibility decisions fail closed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET


SCHEMA_VERSION = 1

EXPECTED_MODEL = "AULA F75 Max Gasket Mechanical Keyboard"
EXPECTED_BUS_NAME = "AULA F75Max"
EXPECTED_VID = "0C45"
EXPECTED_PID = "800A"
EXPECTED_REVISION = "0108"
EXPECTED_CONFIGURATOR_VERSION = "1.0.0.5"
RESPONSE_LEVEL_MIN = 1
RESPONSE_LEVEL_MAX = 5
EXPECTED_CONFIGURATOR_NAME = f"{EXPECTED_MODEL} Driver"
EXPECTED_REGISTRY_DISPLAY_NAME = (
    f"{EXPECTED_MODEL} version {EXPECTED_CONFIGURATOR_VERSION}"
)

PROFILE_DIRECTORY_NAME = f"{EXPECTED_MODEL} Driver Files"
PROFILE_DATABASE_NAME = f"{EXPECTED_MODEL}_datav1.db"

ACTION_USE_MATCHING_CONFIGURATOR = "use_installed_f75max_1_0_0_5"
ACTION_STOP_NO_DEVICE = "stop_no_target_device"
ACTION_STOP_WRONG_HARDWARE = "stop_wrong_hardware"
ACTION_STOP_MISSING_CONFIGURATOR = "stop_missing_configurator"
ACTION_STOP_WRONG_CONFIGURATOR = "stop_wrong_configurator"
ACTION_REPAIR_PROFILE = "repair_profile_state_before_changes"

_HARDWARE_ID_RE = re.compile(
    r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:&REV_([0-9A-F]{4}))?",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
_SAFE_CONFIG_KEYS = (
    "disable_altf4",
    "disable_alttab",
    "disable_win",
    "fn_layer",
    "fn_switch",
    "gamemode",
    "key_respondtime",
    "keyboard_layout",
    "lightmode",
    "sleep_light",
    "sleep_time",
    "version",
)
_COUNT_TABLES = (
    "t_config_data",
    "t_customlight_data",
    "t_customrgb_data",
    "t_key_macro_data",
    "t_ledframe_data",
    "t_ledlayer_data",
    "t_light_data",
    "t_macro_data",
    "t_macrorecord",
    "t_musiclayer_data",
    "t_profile_data",
    "t_userlight_data",
    "t_userlightrgb_data",
)


PNP_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$familyPattern = 'VID_(0C45)&PID_(800A|80B1)|VID_258A&PID_010C|VID_1A2C&PID_9407'
$nodes = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -match $familyPattern } |
    Sort-Object InstanceId)

$roots = @($nodes | Where-Object {
    $_.InstanceId -match '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\'
})
$root = $roots | Where-Object {
    $_.InstanceId -match '^USB\\VID_0C45&PID_800A\\'
} | Select-Object -First 1
if ($null -eq $root) {
    $root = $roots | Select-Object -First 1
}

$propertyMap = [ordered]@{}
$hardwareIds = @()
if ($null -ne $root) {
    $keys = @(
        'DEVPKEY_Device_HardwareIds',
        'DEVPKEY_Device_BusReportedDeviceDesc',
        'DEVPKEY_Device_DeviceDesc',
        'DEVPKEY_Device_Manufacturer',
        'DEVPKEY_Device_DriverVersion',
        'DEVPKEY_Device_DriverProvider',
        'DEVPKEY_Device_DriverInfPath',
        'DEVPKEY_Device_DriverDate'
    )
    foreach ($key in $keys) {
        $property = Get-PnpDeviceProperty -InstanceId $root.InstanceId `
            -KeyName $key -ErrorAction SilentlyContinue
        if ($null -ne $property) {
            $propertyMap[$key] = $property.Data
        }
    }
    if ($propertyMap.Contains('DEVPKEY_Device_HardwareIds')) {
        $hardwareIds = @($propertyMap['DEVPKEY_Device_HardwareIds'])
    }
}

$nodeData = @($nodes | ForEach-Object {
    [pscustomobject]@{
        status = [string]$_.Status
        class = [string]$_.Class
        friendly_name = [string]$_.FriendlyName
        instance_id = [string]$_.InstanceId
    }
})
$rootData = $null
if ($null -ne $root) {
    $rootData = [pscustomobject]@{
        status = [string]$root.Status
        class = [string]$root.Class
        friendly_name = [string]$root.FriendlyName
        instance_id = [string]$root.InstanceId
    }
}

[pscustomobject]@{
    nodes = $nodeData
    root = $rootData
    hardware_ids = @($hardwareIds)
    properties = [pscustomobject]$propertyMap
} | ConvertTo-Json -Depth 8 -Compress
"""


REGISTRY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$locations = @(
    [pscustomobject]@{
        source = 'HKLM64'
        path = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    },
    [pscustomobject]@{
        source = 'HKLM32'
        path = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    },
    [pscustomobject]@{
        source = 'HKCU'
        path = 'Registry::HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    }
)

$entries = @()
foreach ($location in $locations) {
    $entries += @(Get-ItemProperty $location.path -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.DisplayName -match 'AULA|F75|EPOMAKER' } |
        ForEach-Object {
            [pscustomobject]@{
                source = $location.source
                display_name = [string]$_.DisplayName
                display_version = [string]$_.DisplayVersion
                install_location = [string]$_.InstallLocation
                publisher = [string]$_.Publisher
                registry_key = [string]$_.PSChildName
            }
        })
}

[pscustomobject]@{
    entries = @($entries | Sort-Object display_name, display_version, source -Unique)
} | ConvertTo-Json -Depth 6 -Compress
"""


class AuditError(RuntimeError):
    """Raised when a live evidence source cannot be queried or decoded."""


class PowerShellProbe:
    """Read-only Windows evidence probe with injectable executable for tests."""

    def __init__(self, executable: str | None = None, timeout_seconds: int = 15):
        self.executable = executable or _find_powershell()
        self.timeout_seconds = timeout_seconds

    def _run_json(self, script: str) -> Mapping[str, Any]:
        if not self.executable:
            raise AuditError("PowerShell was not found")
        try:
            encoded_command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            completed = subprocess.run(
                [
                    self.executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded_command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AuditError(f"PowerShell query failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise AuditError(
                f"PowerShell exited {completed.returncode}: {detail[:500]}"
            )
        output = completed.stdout.strip().lstrip("\ufeff")
        if not output:
            raise AuditError("PowerShell returned no JSON")
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError as exc:
            raise AuditError(f"PowerShell returned invalid JSON: {output[:500]}") from exc
        if not isinstance(decoded, Mapping):
            raise AuditError("PowerShell JSON root was not an object")
        return decoded

    def device_snapshot(self) -> Mapping[str, Any]:
        return self._run_json(PNP_SCRIPT)

    def installed_configurators(self) -> Mapping[str, Any]:
        return self._run_json(REGISTRY_SCRIPT)


def _find_powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _normalise_hex(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().removeprefix("0X")
    return text or None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _extract_numeric_version(value: Any) -> str | None:
    match = _VERSION_RE.search(_text(value))
    return match.group(0) if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def parse_hardware_identity(hardware_ids: Sequence[Any]) -> dict[str, str | None]:
    """Extract VID, PID, and revision, preferring an ID that includes REV."""

    parsed: list[tuple[str, str, str | None]] = []
    for hardware_id in hardware_ids:
        match = _HARDWARE_ID_RE.search(_text(hardware_id))
        if match:
            parsed.append(
                (
                    match.group(1).upper(),
                    match.group(2).upper(),
                    match.group(3).upper() if match.group(3) else None,
                )
            )
    selected = next((item for item in parsed if item[2]), parsed[0] if parsed else None)
    if not selected:
        return {"vid": None, "pid": None, "revision": None}
    return {"vid": selected[0], "pid": selected[1], "revision": selected[2]}


def normalise_device_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Turn raw PnP JSON into the stable service contract."""

    root = payload.get("root") if isinstance(payload.get("root"), Mapping) else {}
    properties = (
        payload.get("properties")
        if isinstance(payload.get("properties"), Mapping)
        else {}
    )
    hardware_ids = [_text(item) for item in _as_list(payload.get("hardware_ids"))]
    identity = parse_hardware_identity(hardware_ids)
    bus_name = _text(properties.get("DEVPKEY_Device_BusReportedDeviceDesc")) or None
    status = _text(root.get("status")) or None
    present = bool(root)
    exact_identity = (
        present
        and identity["vid"] == EXPECTED_VID
        and identity["pid"] == EXPECTED_PID
        and identity["revision"] == EXPECTED_REVISION
        and bus_name == EXPECTED_BUS_NAME
    )
    nodes = []
    for node in _as_list(payload.get("nodes")):
        if not isinstance(node, Mapping):
            continue
        nodes.append(
            {
                "status": _text(node.get("status")) or None,
                "class": _text(node.get("class")) or None,
                "friendly_name": _text(node.get("friendly_name")) or None,
                "instance_id": _text(node.get("instance_id")) or None,
            }
        )
    return {
        "source": "windows_pnp",
        "present": present,
        "status": status,
        "instance_id": _text(root.get("instance_id")) or None,
        "bus_reported_name": bus_name,
        "hardware_ids": hardware_ids,
        **identity,
        "windows_hid_driver": {
            "provider": _text(properties.get("DEVPKEY_Device_DriverProvider")) or None,
            "version": _text(properties.get("DEVPKEY_Device_DriverVersion")) or None,
            "inf": _text(properties.get("DEVPKEY_Device_DriverInfPath")) or None,
            "date": _text(properties.get("DEVPKEY_Device_DriverDate")) or None,
        },
        "pnp_node_count": len(nodes),
        "pnp_nodes": nodes,
        "exact_match": exact_identity and status == "OK",
    }


def normalise_registry_entries(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in _as_list(payload.get("entries")):
        if not isinstance(raw, Mapping):
            continue
        entries.append(
            {
                "source": _text(raw.get("source")) or None,
                "display_name": _text(raw.get("display_name")) or None,
                "display_version": _text(raw.get("display_version")) or None,
                "install_location": _text(raw.get("install_location")) or None,
                "publisher": _text(raw.get("publisher")) or None,
                "registry_key": _text(raw.get("registry_key")) or None,
            }
        )
    return sorted(
        entries,
        key=lambda item: (
            _text(item["display_name"]).casefold(),
            _text(item["display_version"]),
            _text(item["source"]),
        ),
    )


def select_model_registry_entry(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Select only the F75 Max Gasket configurator, never another F75 branch."""

    expected = EXPECTED_REGISTRY_DISPLAY_NAME.casefold()
    exact = [item for item in entries if _text(item.get("display_name")).casefold() == expected]
    if exact:
        return dict(exact[0])
    model_prefix = EXPECTED_MODEL.casefold()
    same_model = [
        item
        for item in entries
        if _text(item.get("display_name")).casefold().startswith(model_prefix)
    ]
    return dict(same_model[0]) if same_model else None


def inspect_config_xml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "sha256": None,
        "software_name": None,
        "software_version_raw": None,
        "software_version": None,
        "keyboard_name": None,
        "modes": [],
        "usb_target": None,
        "exact_match": False,
        "error": None,
    }
    if not result["exists"]:
        result["error"] = "config.xml is missing"
        return result
    try:
        result["sha256"] = _sha256(path)
        source = path.read_text(encoding="utf-8-sig")
        # The shipped 1.0.0.5 file uses HID interfaces such as
        # ``VID_0C45&PID_800A&MI_00`` without escaping the ampersands.  Preserve
        # the original hash, then repair only bare XML ampersands for parsing.
        parseable = re.sub(
            r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]+;)",
            "&amp;",
            source,
        )
        root = ET.fromstring(parseable)
        software = root.find("software")
        keyboard = root.find("./device.info/keyboard")
        if software is not None:
            result["software_name"] = software.get("name")
            result["software_version_raw"] = software.get("version")
            result["software_version"] = _extract_numeric_version(
                software.get("version")
            )
        if keyboard is not None:
            result["keyboard_name"] = keyboard.get("name")
            for mode in keyboard.findall("mode"):
                parsed_mode = {
                    "value": mode.get("value"),
                    "description": mode.get("desc"),
                    "vid": _normalise_hex(mode.get("vid")),
                    "pid": _normalise_hex(mode.get("pid")),
                    "product_name": mode.get("product_name"),
                    "hid_interface": mode.get("hid_interface"),
                }
                result["modes"].append(parsed_mode)
        result["usb_target"] = next(
            (
                mode
                for mode in result["modes"]
                if mode["vid"] == EXPECTED_VID and mode["pid"] == EXPECTED_PID
            ),
            None,
        )
        target = result["usb_target"] or {}
        result["exact_match"] = (
            result["software_name"] == EXPECTED_CONFIGURATOR_NAME
            and result["software_version"] == EXPECTED_CONFIGURATOR_VERSION
            and result["keyboard_name"] == EXPECTED_MODEL
            and target.get("product_name") == EXPECTED_BUS_NAME
        )
    except (OSError, ET.ParseError) as exc:
        result["error"] = f"config.xml could not be read: {exc}"
    return result


def _default_profile_db(local_app_data: str | os.PathLike[str] | None = None) -> Path:
    root = Path(local_app_data) if local_app_data else Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    return root / PROFILE_DIRECTORY_NAME / "db" / PROFILE_DATABASE_NAME


def _sqlite_table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    }


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def inspect_profile_db(path: Path) -> dict[str, Any]:
    """Read profile and response state through a SQLite read-only URI."""

    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "read_only": True,
        "size_bytes": None,
        "sha256": None,
        "integrity": None,
        "tables": [],
        "row_counts": {},
        "profiles": [],
        "active_profile_count": 0,
        "active_profile": None,
        "active_config": {},
        "response": {
            "key": "key_respondtime",
            "present": False,
            "raw_value": None,
            "level_index": None,
            "valid_level": False,
        },
        "healthy": False,
        "error": None,
    }
    if not result["exists"]:
        result["error"] = "profile database is missing"
        return result
    connection: sqlite3.Connection | None = None
    try:
        result["size_bytes"] = path.stat().st_size
        result["sha256"] = _sha256(path)
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        integrity = [row[0] for row in connection.execute("PRAGMA quick_check")]
        result["integrity"] = integrity
        tables = _sqlite_table_names(connection)
        result["tables"] = sorted(tables)
        for table in _COUNT_TABLES:
            if table in tables:
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0]
                result["row_counts"][table] = count

        if "t_profile_data" in tables:
            rows = connection.execute(
                "SELECT profile, name, status, type "
                "FROM t_profile_data ORDER BY profile"
            )
            result["profiles"] = [dict(row) for row in rows]
        active_profiles = [
            profile for profile in result["profiles"] if profile.get("status") == 1
        ]
        result["active_profile_count"] = len(active_profiles)
        if len(active_profiles) == 1:
            result["active_profile"] = active_profiles[0]

        active_profile_id = (
            result["active_profile"].get("profile")
            if result["active_profile"] is not None
            else None
        )
        if "t_config_data" in tables and active_profile_id is not None:
            placeholders = ",".join("?" for _ in _SAFE_CONFIG_KEYS)
            rows = connection.execute(
                "SELECT key, value FROM t_config_data "
                f"WHERE profile = ? AND key IN ({placeholders}) ORDER BY key",
                (active_profile_id, *_SAFE_CONFIG_KEYS),
            )
            result["active_config"] = {row["key"]: row["value"] for row in rows}
        raw_response = result["active_config"].get("key_respondtime")
        level_index: int | None = None
        if raw_response is not None:
            try:
                level_index = int(raw_response)
            except (TypeError, ValueError):
                level_index = None
        result["response"] = {
            "key": "key_respondtime",
            "present": raw_response is not None,
            "raw_value": raw_response,
            "level_index": level_index,
            "valid_level": (
                level_index is not None
                and RESPONSE_LEVEL_MIN <= level_index <= RESPONSE_LEVEL_MAX
            ),
        }
        result["healthy"] = (
            integrity == ["ok"]
            and result["active_profile_count"] == 1
            and result["response"]["valid_level"]
        )
    except (OSError, sqlite3.Error) as exc:
        result["error"] = f"profile database could not be read: {exc}"
    finally:
        if connection is not None:
            connection.close()
    return result


def recommend_compatibility(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only safe action for normalized compatibility evidence."""

    hardware = evidence.get("hardware", {})
    configurator = evidence.get("configurator", {})
    profile = evidence.get("profile", {})
    if not isinstance(hardware, Mapping):
        hardware = {}
    if not isinstance(configurator, Mapping):
        configurator = {}
    if not isinstance(profile, Mapping):
        profile = {}

    common = {"safe_to_flash_firmware": False}
    if not hardware.get("present"):
        return {
            "action": ACTION_STOP_NO_DEVICE,
            "safe_to_configure": False,
            **common,
            "reasons": ["No present AULA F75-family USB root device was found."],
            "next_step": "Connect the keyboard directly in wired mode and audit again.",
        }

    hardware_mismatches = []
    for key, expected in (
        ("vid", EXPECTED_VID),
        ("pid", EXPECTED_PID),
        ("revision", EXPECTED_REVISION),
        ("bus_reported_name", EXPECTED_BUS_NAME),
    ):
        actual = hardware.get(key)
        if _text(actual).upper() != expected.upper():
            hardware_mismatches.append(f"{key}={actual!r}, expected {expected!r}")
    if hardware_mismatches:
        return {
            "action": ACTION_STOP_WRONG_HARDWARE,
            "safe_to_configure": False,
            **common,
            "reasons": hardware_mismatches,
            "next_step": "Use the software branch for the physically identified model; do not cross-flash.",
        }

    if not configurator.get("installed"):
        return {
            "action": ACTION_STOP_MISSING_CONFIGURATOR,
            "safe_to_configure": False,
            **common,
            "reasons": [f"The matching configurator {EXPECTED_CONFIGURATOR_VERSION} is not installed."],
            "next_step": "Restore or install only the F75 Max Gasket 1.0.0.5 package.",
        }

    configurator_mismatches = []
    for key, expected in (
        ("version", EXPECTED_CONFIGURATOR_VERSION),
        ("config_file_version", EXPECTED_CONFIGURATOR_VERSION),
        ("software_name", EXPECTED_CONFIGURATOR_NAME),
        ("keyboard_name", EXPECTED_MODEL),
        ("target_vid", EXPECTED_VID),
        ("target_pid", EXPECTED_PID),
        ("target_product_name", EXPECTED_BUS_NAME),
    ):
        actual = configurator.get(key)
        if _text(actual).casefold() != expected.casefold():
            configurator_mismatches.append(
                f"configurator.{key}={actual!r}, expected {expected!r}"
            )
    if configurator_mismatches:
        return {
            "action": ACTION_STOP_WRONG_CONFIGURATOR,
            "safe_to_configure": False,
            **common,
            "reasons": configurator_mismatches,
            "next_step": "Remove the mismatched AULA branch and restore the exact 1.0.0.5 package.",
        }

    try:
        response_level = int(profile.get("response_level"))
    except (TypeError, ValueError):
        response_level = None
    response_level_valid = (
        response_level is not None
        and RESPONSE_LEVEL_MIN <= response_level <= RESPONSE_LEVEL_MAX
    )
    if (
        not profile.get("healthy")
        or not profile.get("response_present")
        or not response_level_valid
    ):
        return {
            "action": ACTION_REPAIR_PROFILE,
            "safe_to_configure": False,
            **common,
            "reasons": [
                "The profile database is not healthy with one active profile and a valid response Level 1 through 5."
            ],
            "next_step": "Restore the profile database backup or let the matching configurator recreate it, then audit again.",
        }

    return {
        "action": ACTION_USE_MATCHING_CONFIGURATOR,
        "safe_to_configure": True,
        **common,
        "reasons": [
            "Hardware, revision, configurator target, version, and profile state all match."
        ],
        "next_step": "Use the installed 1.0.0.5 configurator for profile changes; firmware flashing remains out of scope.",
    }


def _expected_contract() -> dict[str, Any]:
    return {
        "model": EXPECTED_MODEL,
        "bus_reported_name": EXPECTED_BUS_NAME,
        "usb": {
            "vid": EXPECTED_VID,
            "pid": EXPECTED_PID,
            "revision": EXPECTED_REVISION,
        },
        "configurator": {
            "name": EXPECTED_CONFIGURATOR_NAME,
            "version": EXPECTED_CONFIGURATOR_VERSION,
        },
    }


def _configurator_contract(
    entries: Sequence[Mapping[str, Any]],
    selected: Mapping[str, Any] | None,
    config_xml: Mapping[str, Any],
) -> dict[str, Any]:
    usb_target = config_xml.get("usb_target")
    if not isinstance(usb_target, Mapping):
        usb_target = {}
    installed = selected is not None
    version = _text(selected.get("display_version")) if selected else None
    exact_match = (
        installed
        and version == EXPECTED_CONFIGURATOR_VERSION
        and bool(config_xml.get("exact_match"))
    )
    return {
        "source": "uninstall_registry_and_config_xml",
        "installed": installed,
        "version": version,
        "config_file_version": config_xml.get("software_version"),
        "display_name": selected.get("display_name") if selected else None,
        "install_location": selected.get("install_location") if selected else None,
        "registry_source": selected.get("source") if selected else None,
        "software_name": config_xml.get("software_name"),
        "keyboard_name": config_xml.get("keyboard_name"),
        "target_vid": usb_target.get("vid"),
        "target_pid": usb_target.get("pid"),
        "target_product_name": usb_target.get("product_name"),
        "exact_match": exact_match,
        "config_xml": dict(config_xml),
        "other_aula_entries": [
            dict(entry)
            for entry in entries
            if selected is None or dict(entry) != dict(selected)
        ],
    }


def collect_audit(
    probe: Any | None = None,
    *,
    config_xml_path: Path | None = None,
    profile_db_path: Path | None = None,
    local_app_data: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Collect all evidence and return the stable JSON-serializable contract."""

    probe = probe or PowerShellProbe()
    errors: list[dict[str, str]] = []
    try:
        device = normalise_device_snapshot(probe.device_snapshot())
    except (AuditError, OSError, ValueError) as exc:
        errors.append({"source": "windows_pnp", "error": str(exc)})
        device = normalise_device_snapshot({})

    try:
        entries = normalise_registry_entries(probe.installed_configurators())
    except (AuditError, OSError, ValueError) as exc:
        errors.append({"source": "uninstall_registry", "error": str(exc)})
        entries = []
    selected = select_model_registry_entry(entries)

    if config_xml_path is None:
        install_location = selected.get("install_location") if selected else None
        config_xml_path = (
            Path(_text(install_location)) / "config.xml"
            if install_location
            else Path("C:/Program Files (x86)") / EXPECTED_MODEL / "config.xml"
        )
    config_xml = inspect_config_xml(Path(config_xml_path))
    if config_xml.get("error"):
        errors.append({"source": "config_xml", "error": _text(config_xml["error"])})
    configurator = _configurator_contract(entries, selected, config_xml)

    profile_path = (
        Path(profile_db_path)
        if profile_db_path is not None
        else _default_profile_db(local_app_data)
    )
    profile_db = inspect_profile_db(profile_path)
    if profile_db.get("error"):
        errors.append({"source": "profile_db", "error": _text(profile_db["error"])})

    evidence = {
        "hardware": {
            "present": device["present"],
            "vid": device["vid"],
            "pid": device["pid"],
            "revision": device["revision"],
            "bus_reported_name": device["bus_reported_name"],
        },
        "configurator": {
            "installed": configurator["installed"],
            "version": configurator["version"],
            "config_file_version": configurator["config_file_version"],
            "software_name": configurator["software_name"],
            "keyboard_name": configurator["keyboard_name"],
            "target_vid": configurator["target_vid"],
            "target_pid": configurator["target_pid"],
            "target_product_name": configurator["target_product_name"],
        },
        "profile": {
            "healthy": profile_db["healthy"],
            "response_present": profile_db["response"]["present"],
            "response_level": profile_db["response"]["level_index"],
        },
    }
    recommendation = recommend_compatibility(evidence)
    checks = {
        "hardware_exact": bool(device["exact_match"]),
        "configurator_exact": bool(configurator["exact_match"]),
        "profile_healthy": bool(profile_db["healthy"]),
        "evidence_queries_clean": not errors,
    }
    ready = all(checks.values()) and recommendation["safe_to_configure"]
    return {
        "schema_version": SCHEMA_VERSION,
        "service": "device_audit",
        "read_only": True,
        "expected": _expected_contract(),
        "device": device,
        "configurator": configurator,
        "profile_db": profile_db,
        "recommendation": recommendation,
        "verdict": {
            "ready_for_efficiency_changes": ready,
            "checks": checks,
        },
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only inventory for the exact AULA F75 Max 0C45:800A REV_0108."
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output.")
    parser.add_argument(
        "--config-xml",
        type=Path,
        help="Override the vendor config.xml path (mainly for diagnostics/tests).",
    )
    parser.add_argument(
        "--profile-db",
        type=Path,
        help="Override the vendor profile database path (mainly for diagnostics/tests).",
    )
    parser.add_argument(
        "--powershell",
        help="PowerShell executable path; defaults to powershell.exe then pwsh.exe.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = collect_audit(
        PowerShellProbe(executable=args.powershell) if args.powershell else None,
        config_xml_path=args.config_xml,
        profile_db_path=args.profile_db,
    )
    print(
        json.dumps(
            snapshot,
            indent=2 if args.pretty else None,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0 if snapshot["verdict"]["ready_for_efficiency_changes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

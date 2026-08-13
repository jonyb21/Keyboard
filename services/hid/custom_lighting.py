"""Named lighting profiles for safe stock effects and per-key experiments.

Each profile has a deployable ``stock`` configuration that uses the verified
F75 Max lighting transaction.  Its ``per_key`` layer is compiled for review
only: the only public F108 reference uses an unverified `04 23` command, so
exact-F75 live per-key application remains capture-gated.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from . import protocol
from .protocol import LightingConfig, ProtocolError


PROFILE_PATH = Path(__file__).with_name("data") / "lighting_profiles.json"
LAYOUT_PATH = Path(__file__).with_name("data") / "f75max_layout.json"
PER_KEY_TABLE_BYTES = 0x240
CAPTURE_REQUIRED = (
    "capture_required: exact-F75 per-key apply is disabled until a live "
    "AULA F75Max 0C45:800A release 0x0108 capture confirms the command, "
    "table framing, trailer, and ACK sequence"
)


@dataclass(frozen=True)
class PerKeyColor:
    name: str
    light_index: int
    red: int
    green: int
    blue: int

    @property
    def color_hex(self) -> str:
        return f"{self.red:02X}{self.green:02X}{self.blue:02X}"


@dataclass(frozen=True)
class CompiledProfile:
    name: str
    slug: str
    description: str
    stock: LightingConfig
    keys: tuple[PerKeyColor, ...]
    table: bytes
    table_sha256: str


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot load lighting data {path}: {exc}") from exc


def _normalized(text: str) -> str:
    return "-".join(text.strip().lower().replace("_", "-").split())


def _parse_profile_color(text: Any, context: str) -> tuple[int, int, int]:
    if not isinstance(text, str):
        raise ProtocolError(f"{context} color must be RRGGBB text")
    try:
        return protocol.parse_color(text)
    except ProtocolError as exc:
        raise ProtocolError(f"{context}: {exc}") from exc


def available_profiles(path: Path = PROFILE_PATH) -> tuple[str, ...]:
    data = _load_json(path)
    profiles = data.get("profiles", []) if isinstance(data, dict) else []
    return tuple(item.get("name", "") for item in profiles if isinstance(item, dict))


def compile_profile(
    profile_name: str,
    *,
    profile_path: Path = PROFILE_PATH,
    layout_path: Path = LAYOUT_PATH,
) -> CompiledProfile:
    """Validate and compile a named text profile into indexed RGB slots."""

    data = _load_json(profile_path)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ProtocolError("lighting profile schema_version must be 1")
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ProtocolError("lighting profiles must be a non-empty list")

    wanted = _normalized(profile_name)
    matches = [
        item
        for item in raw_profiles
        if isinstance(item, dict)
        and wanted in {_normalized(str(item.get("name", ""))), _normalized(str(item.get("slug", "")))}
    ]
    if len(matches) != 1:
        choices = ", ".join(
            str(item.get("name")) for item in raw_profiles if isinstance(item, dict)
        )
        raise ProtocolError(f"unknown lighting profile {profile_name!r}; choose {choices}")
    raw = matches[0]

    name = raw.get("name")
    slug = raw.get("slug")
    description = raw.get("description")
    if not all(isinstance(value, str) and value.strip() for value in (name, slug, description)):
        raise ProtocolError("profile name, slug, and description must be non-empty text")

    stock_raw = raw.get("stock")
    if not isinstance(stock_raw, dict):
        raise ProtocolError(f"{name} stock must be an object")
    for field, lower, upper in (
        ("brightness", 0, protocol.BRIGHTNESS_MAX),
        ("speed", 0, protocol.SPEED_MAX),
        ("direction", 0, 1),
    ):
        value = stock_raw.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{name} stock {field} must be an integer")
        if not lower <= value <= upper:
            raise ProtocolError(
                f"{name} stock {field} must be from {lower} through {upper}"
            )
    if not isinstance(stock_raw.get("colorful"), bool):
        raise ProtocolError(f"{name} stock colorful must be boolean")
    red, green, blue = _parse_profile_color(stock_raw.get("color"), f"{name} stock")
    stock = LightingConfig(
        mode=protocol.resolve_mode(str(stock_raw.get("mode", ""))),
        red=red,
        green=green,
        blue=blue,
        brightness=stock_raw.get("brightness"),
        speed=stock_raw.get("speed"),
        direction=stock_raw.get("direction"),
        colorful=stock_raw.get("colorful"),
    ).validate()

    layout = _load_json(layout_path)
    raw_keys = layout.get("keys") if isinstance(layout, dict) else None
    if not isinstance(raw_keys, list) or not raw_keys:
        raise ProtocolError("canonical layout keys must be a non-empty list")
    by_name: dict[str, dict] = {}
    indices: set[int] = set()
    for item in raw_keys:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ProtocolError("each canonical layout key needs a text name")
        light_index = item.get("light_index")
        if isinstance(light_index, bool) or not isinstance(light_index, int):
            raise ProtocolError(f"layout key {item['name']} has no integer light_index")
        if not 1 <= light_index <= 142 or light_index in indices:
            raise ProtocolError(f"invalid or duplicate light_index {light_index}")
        indices.add(light_index)
        by_name[item["name"]] = item

    per_key = raw.get("per_key")
    if not isinstance(per_key, dict):
        raise ProtocolError(f"{name} per_key must be an object")
    default = _parse_profile_color(per_key.get("default"), f"{name} per_key default")
    colors = {key_name: default for key_name in by_name}
    seen_assignments: set[str] = set()
    groups = per_key.get("groups")
    if not isinstance(groups, list):
        raise ProtocolError(f"{name} per_key groups must be a list")
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict) or not isinstance(group.get("keys"), list):
            raise ProtocolError(f"{name} group {group_index} must contain a keys list")
        color = _parse_profile_color(group.get("color"), f"{name} group {group_index}")
        for key_name in group["keys"]:
            if key_name not in by_name:
                raise ProtocolError(f"{name} references unknown key {key_name!r}")
            if key_name in seen_assignments:
                raise ProtocolError(f"{name} assigns key {key_name!r} more than once")
            seen_assignments.add(key_name)
            colors[key_name] = color

    compiled_keys = tuple(
        PerKeyColor(
            name=key_name,
            light_index=int(item["light_index"]),
            red=colors[key_name][0],
            green=colors[key_name][1],
            blue=colors[key_name][2],
        )
        for key_name, item in sorted(by_name.items(), key=lambda pair: pair[1]["light_index"])
    )
    table = bytearray(PER_KEY_TABLE_BYTES)
    for key in compiled_keys:
        offset = key.light_index * 4
        table[offset : offset + 4] = bytes(
            (key.light_index, key.red, key.green, key.blue)
        )
    table_bytes = bytes(table)
    return CompiledProfile(
        name=name,
        slug=slug,
        description=description,
        stock=stock,
        keys=compiled_keys,
        table=table_bytes,
        table_sha256=hashlib.sha256(table_bytes).hexdigest(),
    )


def require_per_key_capture() -> None:
    """Fail closed for the intentionally unavailable exact-F75 wire path."""

    raise ProtocolError(CAPTURE_REQUIRED)

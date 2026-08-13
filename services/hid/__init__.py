"""AULA F75 Max stock-firmware HID protocol client.

Modules:
    protocol -- pure payload builders/parsers (no I/O)
    device   -- discovery / endpoint classification over hid enumerations
    client   -- transport layer with 35 ms pacing (injectable transport)
    screen   -- 128x128 LCD image preparation and RGB565LE encoding
    custom_lighting -- named stock presets and capture-gated per-key compiler
    cli      -- status, battery, light, clock, screen, and remap commands

Wire protocol contract: contracts/hid_protocol.md (repo root).
"""

__version__ = "1.3.0"

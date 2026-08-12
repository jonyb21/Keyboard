"""AULA F75 Max stock-firmware HID protocol client.

Modules:
    protocol -- pure payload builders/parsers (no I/O)
    device   -- discovery / endpoint classification over hid enumerations
    client   -- transport layer with 35 ms pacing (injectable transport)
    cli      -- command line interface (status, battery, light, remap)

Wire protocol contract: contracts/hid_protocol.md (repo root).
"""

__version__ = "1.2.0"

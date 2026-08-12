"""Transport layer for the AULA F75 Max: 35 ms pacing, injectable transport.

The ONLY place allowed to touch a real device is `HidapiTransport`. Everything
else operates on the abstract `Transport` interface, so tests (and the
orchestrator's dry runs) inject mocks and never open hardware.

Sequences implemented here follow contracts/hid_protocol.md sections 4-5.
"""

from __future__ import annotations

import time

from . import device as device_model
from . import protocol
from .protocol import LightingConfig, ProtocolError


class AckError(RuntimeError):
    """A wired readback did not acknowledge the command (byte[3] != 0x01)."""


class DeviceIdentityError(ProtocolError):
    """A real transport was requested without an exact discovered endpoint."""


class Transport:
    """Abstract transport. Implementations own exactly one HID handle.

    Payloads carry no report-ID byte; implementations add one if their
    backend requires it (contracts/hid_protocol.md section 3).
    """

    def send_feature(self, payload: bytes) -> None:
        raise NotImplementedError

    def get_feature(self, length: int = protocol.WIRED_REPORT_SIZE) -> bytes:
        raise NotImplementedError

    def write_output(self, payload: bytes) -> None:
        raise NotImplementedError

    def read_input(self, timeout_ms: int) -> bytes | None:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


class Pacer:
    """Enforces the 35 ms inter-command gap with an injectable clock.

    Vendor config.xml cmd_delaytime=35; [F108] pkg/aula/device.go sleeps
    35 ms after every SetFeature/GetFeature. We pace *before* each transfer
    so the first command is never delayed.
    """

    def __init__(
        self,
        interval_s: float = protocol.COMMAND_DELAY_S,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self.interval_s = interval_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._last: float | None = None

    def pace(self) -> None:
        now = self._monotonic()
        if self._last is not None:
            remaining = self.interval_s - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last = now


class Client:
    """Protocol client over an injected Transport."""

    def __init__(
        self,
        transport: Transport,
        monotonic=time.monotonic,
        sleep=time.sleep,
        strict_ack: bool = True,
    ) -> None:
        self._transport = transport
        self._pacer = Pacer(monotonic=monotonic, sleep=sleep)
        self._monotonic = monotonic
        self._strict_ack = strict_ack

    def close(self) -> None:
        self._transport.close()

    # -- paced primitives ---------------------------------------------------

    def _send_feature(self, payload: bytes) -> None:
        self._pacer.pace()
        self._transport.send_feature(payload)

    def _readback(self, command: bytes) -> bytes:
        self._pacer.pace()
        response = self._transport.get_feature(protocol.WIRED_REPORT_SIZE)
        if self._strict_ack and not protocol.parse_wired_ack(response, command):
            raise AckError(
                f"no ACK for command {command[0]:02x} {command[1]:02x}: "
                f"response {bytes(response[:8]).hex(' ')}"
            )
        return response

    def _write_output(self, payload: bytes) -> None:
        self._pacer.pace()
        self._transport.write_output(payload)

    # -- wired flows (feature reports on the 0xFF13 collection) -------------

    def set_lighting_wired(self, config: LightingConfig) -> None:
        """begin -> lighting init -> data -> apply -> finalize.

        [F108] pkg/aula/lighting.go SetLighting sequence; readback steps per
        ai-docs/hid-protocol.md "Lighting Mode / Effect" table.
        """
        config.validate()
        begin = protocol.build_wired_begin()
        self._send_feature(begin)
        self._readback(begin)

        init = protocol.build_wired_lighting_init()
        self._send_feature(init)
        self._readback(init)

        self._send_feature(protocol.build_wired_lighting_data(config))

        apply_cmd = protocol.build_wired_apply()
        self._send_feature(apply_cmd)
        self._readback(apply_cmd)

        self._send_feature(protocol.build_wired_finalize())

    def sync_clock_wired(self, when: time.struct_time) -> None:
        """begin -> clock init -> data -> apply, all with readback.

        [OSX] AulaF75Bar/main.m clock flow (verified working there).
        struct_time.tm_wday is Monday=0; the wire wants Sunday=0.
        """
        begin = protocol.build_wired_begin()
        self._send_feature(begin)
        self._readback(begin)

        init = protocol.build_wired_clock_init()
        self._send_feature(init)
        self._readback(init)

        weekday = (when.tm_wday + 1) % 7  # Monday=0 -> Sunday=0 convention.
        data = protocol.build_wired_clock_data(
            when.tm_year,
            when.tm_mon,
            when.tm_mday,
            when.tm_hour,
            when.tm_min,
            when.tm_sec,
            weekday,
        )
        self._send_feature(data)
        self._readback(data)

        apply_cmd = protocol.build_wired_apply()
        self._send_feature(apply_cmd)
        self._readback(apply_cmd)

    def apply_remap(self, remaps, fn_layer: bool = False) -> None:
        """begin -> remap init -> 9 table packets -> apply -> finalize.

        [F108] pkg/aula/remap.go sendRemapTable (hardware-verified there via
        `aula.exe remap`): begin `04 18` (readback), init `04 11` normal /
        `04 27` FN with byte[8]=0x09 (readback), the 576-byte table as nine
        paced feature reports with NO readback (the repo's
        key-remap-protocol.md doc claims a readback after the last packet,
        but the verified code passes readback=false to sendMultiPacket — we
        follow the code), apply `04 02` (readback), then finalize `04 F0`
        WITH readback (remap.go sendCommand(0x04 0xF0, true) — unlike the
        lighting flow's readback-free finalizeTransaction).
        """
        table = protocol.build_remap_table(remaps)

        begin = protocol.build_wired_begin()
        self._send_feature(begin)
        self._readback(begin)

        init = protocol.build_wired_remap_init(fn_layer)
        self._send_feature(init)
        self._readback(init)

        for packet in protocol.split_remap_table(table):
            self._send_feature(packet)

        apply_cmd = protocol.build_wired_apply()
        self._send_feature(apply_cmd)
        self._readback(apply_cmd)

        finalize = protocol.build_wired_finalize()
        self._send_feature(finalize)
        self._readback(finalize)

    def reset_remap(self, fn_layer: bool = False) -> None:
        """Clear every remap on one layer by sending the all-zero table.

        [F108] pkg/aula/remap.go ResetKeyRemap / ResetFnKeyRemap: identical
        transaction with no remap slots set ("A slot of 00 00 00 00 means no
        remap", ai-docs/key-remap-protocol.md).
        """
        self.apply_remap((), fn_layer=fn_layer)

    # -- dongle flows (32-byte output reports on the 0xFF60 collection) -----

    def set_lighting_dongle(
        self, config: LightingConfig, send_commit: bool = False
    ) -> None:
        """Single all-in-one packet; optional commit precursor.

        [OSX] F75Probe/main.m sendWirelessRGBLEDModeReports (commit optional,
        probe-only in the source).
        """
        config.validate()
        if send_commit:
            self._write_output(protocol.build_dongle_commit())
        self._write_output(protocol.build_dongle_lighting(config))

    def read_battery(self, timeout_s: float = 1.25) -> int:
        """Request and read the battery percent over the dongle.

        [OSX] AulaF75Bar/main.m BatteryFromAulaRawHID: write the 20 01
        request, then collect input reports until one parses, with a ~1.25 s
        deadline.
        """
        self._write_output(protocol.build_battery_request())
        deadline = self._monotonic() + timeout_s
        while True:
            now = self._monotonic()
            remaining_ms = int((deadline - now) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError(
                    f"no battery response within {timeout_s:.2f}s"
                )
            report = self._transport.read_input(min(remaining_ms, 100))
            if not report:
                continue
            percent = protocol.parse_battery_response(report)
            if percent is not None:
                return percent


class HidapiTransport(Transport):
    """Real-device transport over the `hidapi` package (`import hid`).

    THE ONLY CODE PATH THAT OPENS HARDWARE. It accepts only a discovered config
    Endpoint and rechecks exact wired/dongle identity before importing hidapi.
    Tests exercise rejection paths but never reach the device open.

    Report-ID handling (contracts/hid_protocol.md section 3): neither config
    collection declares a report ID, so a 0x00 byte is prepended on write
    paths and stripped from feature reads, matching
    [F108] pkg/aula/transport_windows.go and hidapi conventions.
    """

    def __init__(self, endpoint: device_model.Endpoint) -> None:
        if not isinstance(endpoint, device_model.Endpoint):
            raise DeviceIdentityError(
                "HidapiTransport requires an Endpoint returned by "
                "services.hid.device.discover; raw paths are rejected"
            )
        if endpoint.kind == device_model.KIND_WIRED_CONFIG:
            if not endpoint.is_exact_wired_target:
                raise DeviceIdentityError(
                    "refusing wired transport: expected AULA F75Max "
                    "0C45:800A release 0x0108"
                )
        elif endpoint.kind == device_model.KIND_DONGLE_CONFIG:
            if not endpoint.is_exact_dongle_target:
                raise DeviceIdentityError(
                    "refusing dongle transport: expected 05AC:024F "
                    "usage page 0xFF60 usage 0x61"
                )
        else:
            raise DeviceIdentityError(
                f"endpoint kind {endpoint.kind!r} is not a supported config transport"
            )

        import hid  # noqa: PLC0415  (deliberate lazy import)

        raw_path = (
            endpoint.path.encode()
            if isinstance(endpoint.path, str)
            else endpoint.path
        )
        self._device = hid.device()
        self._device.open_path(raw_path)

    def send_feature(self, payload: bytes) -> None:
        if len(payload) != protocol.WIRED_REPORT_SIZE:
            raise ProtocolError(
                f"feature payload must be {protocol.WIRED_REPORT_SIZE} bytes"
            )
        self._device.send_feature_report(b"\x00" + payload)

    def get_feature(self, length: int = protocol.WIRED_REPORT_SIZE) -> bytes:
        data = bytes(self._device.get_feature_report(0x00, length + 1))
        if data[:1] == b"\x00":
            data = data[1:]
        return data

    def write_output(self, payload: bytes) -> None:
        self._device.write(b"\x00" + payload)

    def read_input(self, timeout_ms: int) -> bytes | None:
        data = self._device.read(protocol.WIRED_REPORT_SIZE, timeout_ms)
        return bytes(data) if data else None

    def close(self) -> None:
        self._device.close()

"""Transport layer for the AULA F75 Max: 35 ms pacing, injectable transport.

The ONLY place allowed to touch a real device is `HidapiTransport`. Everything
else operates on the abstract `Transport` interface, so tests (and the
orchestrator's dry runs) inject mocks and never open hardware.

Sequences implemented here follow contracts/hid_protocol.md sections 4-5.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

from . import device as device_model
from . import protocol, screen as screen_model
from .protocol import LightingConfig, ProtocolError


class AckError(RuntimeError):
    """A wired readback did not match the command's ACK semantics."""


class DeviceIdentityError(ProtocolError):
    """A real transport was requested without an exact discovered endpoint."""


class ScreenUploadError(RuntimeError):
    """An LCD upload failed, including best-effort apply cleanup details."""

    def __init__(
        self,
        message: str,
        *,
        page_index: int | None = None,
        cleanup_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.page_index = page_index
        self.cleanup_error = cleanup_error


@dataclass(frozen=True)
class ScreenUploadResult:
    """Trace evidence returned from a successful LCD transfer."""

    page_count: int
    ack_prefix: bytes
    page_ack_prefixes: tuple[bytes, ...]


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

    def _readback_payload_echo(self, command: bytes) -> bytes:
        """Read and validate the exact payload echo used by clock data."""
        self._pacer.pace()
        response = self._transport.get_feature(protocol.WIRED_REPORT_SIZE)
        if self._strict_ack and not protocol.parse_wired_payload_echo(
            response, command
        ):
            raise AckError(
                f"no payload echo for command {command[0]:02x} {command[1]:02x}: "
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
        Begin, init, and apply require status ACKs. Jon's exact F75 Max echoes
        the full `00 01` clock-data payload instead of putting status in byte 3.
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
        self._readback_payload_echo(data)

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


class ScreenClient:
    """Exact F75 Max dual-handle LCD uploader.

    MI_03 carries feature commands and ACKs. MI_02 carries only 4096-byte
    output pages plus fresh input ACKs. The final `04 02` is attempted after
    every successfully sent begin, including uncertain begin ACKs and page
    failures. It is not sent after a definite begin-send failure; `04 F0` is
    never sent.
    """

    PAGE_ACK_TIMEOUT_S = 0.350
    PAGE_PACING_S = 0.005
    # hidapi 0.15 treats timeout_ms=0 as "no timeout", not a nonblocking read.
    # A one-millisecond poll preserves stale-input draining without allowing a
    # page to hang forever. The fixed report cap bounds each drain to 32 ms.
    STALE_DRAIN_POLL_MS = 1
    STALE_DRAIN_MAX_REPORTS = 32

    def __init__(
        self,
        control_transport: Transport,
        screen_transport: Transport,
        *,
        monotonic=time.monotonic,
        sleep=time.sleep,
        strict_ack: bool = True,
        allow_ack_learning: bool = False,
    ) -> None:
        if control_transport is screen_transport:
            raise DeviceIdentityError("screen upload requires two distinct HID handles")
        self._control = Client(
            control_transport,
            monotonic=monotonic,
            sleep=sleep,
            strict_ack=strict_ack,
        )
        self._screen = screen_transport
        self._monotonic = monotonic
        self._sleep = sleep
        self._allow_ack_learning = allow_ack_learning
        self._closed = False

    @classmethod
    def open_exact(
        cls,
        discovery: device_model.Discovery,
        transport_factory,
        **kwargs,
    ) -> "ScreenClient":
        """Validate the complete pair before opening either hardware handle."""

        control_endpoint, screen_endpoint = device_model.require_exact_screen_pair(
            discovery
        )
        control = transport_factory(control_endpoint)
        try:
            screen = transport_factory(screen_endpoint)
        except Exception:
            control.close()
            raise
        try:
            return cls(control, screen, **kwargs)
        except Exception:
            try:
                screen.close()
            finally:
                if control is not screen:
                    control.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._screen.close()
        finally:
            self._control.close()

    def _exchange(self, command: bytes) -> None:
        self._control._send_feature(command)
        self._control._readback(command)

    def _drain_stale_screen_input(self) -> None:
        # A response already queued before this page cannot acknowledge it.
        for _ in range(self.STALE_DRAIN_MAX_REPORTS):
            if not self._screen.read_input(self.STALE_DRAIN_POLL_MS):
                return
        raise ScreenUploadError("screen endpoint stale-input queue did not drain")

    def _read_fresh_page_ack(self, page_index: int) -> bytes:
        deadline = self._monotonic() + self.PAGE_ACK_TIMEOUT_S
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"screen page {page_index + 1} ACK timed out after 350 ms"
                )
            timeout_ms = max(1, min(350, int(remaining * 1000 + 0.999)))
            before = self._monotonic()
            response = self._screen.read_input(timeout_ms)
            if response and len(response) >= 3:
                return bytes(response)
            # A backend may return a short report immediately. Advance fake or
            # real time slightly to guarantee a bounded retry loop.
            if self._monotonic() <= before:
                self._sleep(min(0.001, remaining))

    def upload(self, stream: bytes) -> ScreenUploadResult:
        """Upload a prepared stream and return traceable per-page ACK evidence."""

        # Freeze mutable bytes-like inputs once so the validated stream is the
        # exact stream sent after pacing callbacks and blocking reads.
        stream = bytes(stream)
        try:
            geometry = screen_model.validate_stream(stream)
        except screen_model.ScreenError as exc:
            raise ProtocolError(str(exc)) from exc
        page_count = geometry.page_count
        if protocol.SCREEN_ACK_PREFIX is None and not self._allow_ack_learning:
            raise ScreenUploadError(
                "screen ACK prefix is not pinned; only screen test-pattern may "
                "learn it before normal upload or restore"
            )

        begin_sent = False
        transaction_active = False
        page_index: int | None = None
        primary_error: Exception | None = None
        cleanup_error: Exception | None = None
        ack_prefix: bytes | None = protocol.SCREEN_ACK_PREFIX
        page_ack_prefixes: list[bytes] = []
        try:
            begin = protocol.build_wired_begin()
            self._control._send_feature(begin)
            begin_sent = True
            # The firmware may have entered a transaction even if the following
            # readback fails, so a completed send makes cleanup conservative.
            transaction_active = begin_sent
            self._control._readback(begin)
            self._sleep(0.200)

            metadata = protocol.build_wired_screen_init(page_count)
            self._exchange(metadata)
            self._sleep(0.050)

            for page_index in range(page_count):
                page = stream[page_index * 4096 : (page_index + 1) * 4096]
                self._drain_stale_screen_input()
                self._screen.write_output(page)
                response = self._read_fresh_page_ack(page_index)
                prefix = response[:3]
                if ack_prefix is None:
                    ack_prefix = prefix
                elif prefix != ack_prefix:
                    raise ScreenUploadError(
                        f"screen page {page_index + 1} ACK prefix "
                        f"{prefix.hex(' ')} differs from learned "
                        f"or pinned {ack_prefix.hex(' ')}",
                        page_index=page_index,
                    )
                page_ack_prefixes.append(prefix)
                self._sleep(self.PAGE_PACING_S)
        except Exception as exc:
            primary_error = exc
        finally:
            if transaction_active:
                try:
                    self._sleep(0.100)
                    self._exchange(protocol.build_wired_apply())
                except Exception as exc:
                    cleanup_error = exc

        if primary_error is not None:
            if transaction_active:
                suffix = (
                    f"; final apply also failed: {cleanup_error}"
                    if cleanup_error is not None
                    else "; final apply cleanup acknowledged"
                )
            else:
                suffix = "; final apply not sent because begin send did not complete"
            raise ScreenUploadError(
                f"screen upload failed: {primary_error}{suffix}",
                page_index=page_index,
                cleanup_error=cleanup_error,
            ) from primary_error
        if cleanup_error is not None:
            raise ScreenUploadError(
                f"screen pages sent but final apply failed: {cleanup_error}",
                page_index=page_index,
                cleanup_error=cleanup_error,
            ) from cleanup_error
        assert ack_prefix is not None
        return ScreenUploadResult(
            page_count=page_count,
            ack_prefix=ack_prefix,
            page_ack_prefixes=tuple(page_ack_prefixes),
        )


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
            if not endpoint.is_exact_wired_config:
                raise DeviceIdentityError(
                    "refusing wired transport: expected AULA F75Max "
                    "0C45:800A release 0x0108"
                )
        elif endpoint.kind == device_model.KIND_WIRED_SCREEN:
            if not endpoint.is_exact_wired_screen:
                raise DeviceIdentityError(
                    "refusing screen transport: expected AULA F75Max "
                    "0C45:800A release 0x0108 MI_02 usage page 0xFF68"
                )
        elif endpoint.kind == device_model.KIND_DONGLE_CONFIG:
            if not endpoint.is_exact_dongle_target:
                raise DeviceIdentityError(
                    "refusing dongle transport: expected 05AC:024F "
                    "usage page 0xFF60 usage 0x61"
                )
        else:
            raise DeviceIdentityError(
                f"endpoint kind {endpoint.kind!r} is not a supported transport"
            )

        import hid  # noqa: PLC0415  (deliberate lazy import)

        raw_path = (
            endpoint.path.encode()
            if isinstance(endpoint.path, str)
            else endpoint.path
        )
        self._device = hid.device()
        self._device.open_path(raw_path)
        self._kind = endpoint.kind

    def send_feature(self, payload: bytes) -> None:
        if self._kind != device_model.KIND_WIRED_CONFIG:
            raise ProtocolError("feature reports are allowed only on MI_03 control")
        if len(payload) != protocol.WIRED_REPORT_SIZE:
            raise ProtocolError(
                f"feature payload must be {protocol.WIRED_REPORT_SIZE} bytes"
            )
        report = b"\x00" + payload
        written = self._device.send_feature_report(report)
        self._require_exact_write_count("feature report", written, len(report))

    def get_feature(self, length: int = protocol.WIRED_REPORT_SIZE) -> bytes:
        if self._kind != device_model.KIND_WIRED_CONFIG:
            raise ProtocolError("feature reports are allowed only on MI_03 control")
        data = bytes(self._device.get_feature_report(0x00, length + 1))
        if data[:1] == b"\x00":
            data = data[1:]
        return data

    def write_output(self, payload: bytes) -> None:
        expected = (
            4096
            if self._kind == device_model.KIND_WIRED_SCREEN
            else protocol.DONGLE_REPORT_SIZE
        )
        if len(payload) != expected:
            raise ProtocolError(
                f"{self._kind} output payload must be {expected} bytes; got {len(payload)}"
            )
        report = b"\x00" + payload
        written = self._device.write(report)
        self._require_exact_write_count(
            f"{self._kind} output report", written, len(report)
        )

    @staticmethod
    def _require_exact_write_count(
        operation: str, result: object, expected: int
    ) -> None:
        """Reject hidapi errors and partial/impossible write counts."""
        if (
            isinstance(result, bool)
            or not isinstance(result, int)
            or result != expected
        ):
            raise ProtocolError(
                f"{operation} write returned {result!r}; "
                f"expected exactly {expected} bytes"
            )

    def read_input(self, timeout_ms: int) -> bytes | None:
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or timeout_ms <= 0
        ):
            raise ProtocolError("input read requires a positive timeout in milliseconds")
        data = self._device.read(protocol.WIRED_REPORT_SIZE, timeout_ms)
        return bytes(data) if data else None

    def close(self) -> None:
        self._device.close()

"""AULA F75 Max LCD image preparation.

This module deliberately imports Pillow only inside image-facing functions so
device discovery, remapping, and lighting remain usable without the optional
media dependency.  The wire image is a 256-byte header containing the frame
count and delay table, followed by one 128x128 RGB565LE frame per entry and
zero padding to 4096-byte pages.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


LCD_WIDTH = 128
LCD_HEIGHT = 128
RGB565_FRAME_BYTES = LCD_WIDTH * LCD_HEIGHT * 2
DELAY_HEADER_BYTES = 256
LCD_PAGE_BYTES = 4096
MIN_FRAMES = 1
MAX_FRAMES = 255
STILL_DELAY = 255
SUPPORTED_FORMATS = frozenset({"PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP"})
FIT_MODES = frozenset({"contain", "cover", "stretch"})
LCD_ASSET_MANIFEST = Path(__file__).with_name("data") / "lcd_assets.json"


class ScreenError(ValueError):
    """Raised when an LCD source cannot be represented safely."""


@dataclass(frozen=True)
class ScreenGeometry:
    """Validated frame and page counts from a prepared wire stream."""

    frame_count: int
    page_count: int


@dataclass(frozen=True)
class PreparedScreen:
    """A fully prepared, page-aligned LCD transfer."""

    source_name: str
    source_format: str
    source_size: tuple[int, int]
    source_frame_count: int
    frame_count: int
    delays: tuple[int, ...]
    fit: str
    source_sha256: str
    payload_sha256: str
    stream_sha256: str
    unpadded_bytes: int
    page_count: int
    stream: bytes

    def metadata(self) -> dict[str, Any]:
        """Return stable JSON-compatible transfer metadata without image data."""

        return {
            "source_name": self.source_name,
            "source_format": self.source_format,
            "source_size": list(self.source_size),
            "source_frame_count": self.source_frame_count,
            "frame_count": self.frame_count,
            "delays": list(self.delays),
            "fit": self.fit,
            "source_sha256": self.source_sha256,
            "payload_sha256": self.payload_sha256,
            "stream_sha256": self.stream_sha256,
            "unpadded_bytes": self.unpadded_bytes,
            "page_bytes": LCD_PAGE_BYTES,
            "page_count": self.page_count,
            "stream_bytes": len(self.stream),
        }

    def metadata_json(self) -> str:
        return json.dumps(self.metadata(), indent=2, sort_keys=True)

    def pages(self) -> tuple[bytes, ...]:
        return tuple(
            self.stream[offset : offset + LCD_PAGE_BYTES]
            for offset in range(0, len(self.stream), LCD_PAGE_BYTES)
        )


def _pillow() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - environment-specific text
        raise ScreenError(
            "Pillow is required for LCD images; install services/hid/requirements.txt"
        ) from exc
    return Image, ImageOps, UnidentifiedImageError


def _validate_fit(fit: str) -> str:
    normalized = fit.strip().lower()
    if normalized not in FIT_MODES:
        raise ScreenError(
            f"fit must be one of {', '.join(sorted(FIT_MODES))}; got {fit!r}"
        )
    return normalized


def _validate_frame_limit(max_frames: int) -> int:
    if isinstance(max_frames, bool) or not isinstance(max_frames, int):
        raise ScreenError("max_frames must be an integer from 1 through 255")
    if not MIN_FRAMES <= max_frames <= MAX_FRAMES:
        raise ScreenError("max_frames must be from 1 through 255")
    return max_frames


def delay_byte(duration_ms: int | float | None) -> int:
    """Convert a GIF delay to the vendor two-millisecond unit.

    The vendor converter computes ``seconds * 500`` (500 units per second,
    therefore 2 ms per unit). Positive half values are
    rounded up, then clamped to the one-byte 1..255 range. Missing or
    nonpositive durations use the exact-source 10 ms fallback (wire byte 5).
    Thus the vendor UI's 30..500 ms range maps to 15..250.
    """

    if duration_ms is None:
        duration_ms = 10
    try:
        milliseconds = float(duration_ms)
    except (TypeError, ValueError) as exc:
        raise ScreenError(f"invalid frame duration: {duration_ms!r}") from exc
    if milliseconds <= 0:
        milliseconds = 10
    converted = int(milliseconds / 2.0 + 0.5)
    return max(1, min(255, converted))


def validate_stream(stream: bytes) -> ScreenGeometry:
    """Validate the exact F75 Max LCD header, frames, and zero padding."""

    data = stream if isinstance(stream, bytes) else bytes(stream)
    if not data:
        raise ScreenError("screen frame count must be from 1 through 255")
    frame_count = data[0]
    if not MIN_FRAMES <= frame_count <= MAX_FRAMES:
        raise ScreenError("screen frame count must be from 1 through 255")

    expected_pages = 1 + 8 * frame_count
    if expected_pages > 2041:
        raise ScreenError("screen page count exceeds the 2041-page maximum")
    if len(data) % LCD_PAGE_BYTES or len(data) // LCD_PAGE_BYTES != expected_pages:
        actual = len(data) / LCD_PAGE_BYTES
        raise ScreenError(
            f"screen page count must be {expected_pages} for {frame_count} frames; "
            f"got {actual:g}"
        )
    if any(delay == 0 for delay in data[1 : 1 + frame_count]):
        raise ScreenError("screen delay bytes must be nonzero")
    if any(data[1 + frame_count : DELAY_HEADER_BYTES]):
        raise ScreenError("screen header filler must be zero")

    payload_end = DELAY_HEADER_BYTES + frame_count * RGB565_FRAME_BYTES
    if any(data[payload_end:]):
        raise ScreenError("screen tail padding must be zero")
    return ScreenGeometry(frame_count=frame_count, page_count=expected_pages)


def load_lcd_manifest(path: str | Path = LCD_ASSET_MANIFEST) -> dict[str, Any]:
    """Load the committed text manifest used by the default restore path."""

    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreenError(f"cannot load LCD asset manifest {manifest_path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ScreenError("LCD asset manifest schema_version must be 1")
    if not isinstance(value.get("restore"), dict):
        raise ScreenError("LCD asset manifest has no restore object")
    return value


def validate_restore_manifest(
    prepared: PreparedScreen,
    manifest: dict[str, Any],
    candidate_delay: int,
) -> None:
    """Fail closed unless the default 251-frame restore matches its manifest."""

    restore = manifest.get("restore") if isinstance(manifest, dict) else None
    if not isinstance(restore, dict):
        raise ScreenError("LCD restore manifest is missing")
    expected_size = restore.get("source_size")
    if expected_size != [128, 128] or prepared.source_size != (128, 128):
        raise ScreenError("default restore dimensions must be exactly 128x128")
    expected_count = restore.get("frame_count")
    if (
        expected_count != 251
        or prepared.source_frame_count != 251
        or prepared.frame_count != 251
    ):
        raise ScreenError("default restore must contain exactly 251 frames")
    expected_delay = restore.get("candidate_delay_byte")
    if (
        isinstance(candidate_delay, bool)
        or isinstance(expected_delay, bool)
        or not isinstance(expected_delay, int)
        or not 1 <= expected_delay <= 255
        or expected_delay != candidate_delay
        or len(prepared.delays) != 251
        or any(delay != candidate_delay for delay in prepared.delays)
    ):
        raise ScreenError("default restore candidate delay does not match manifest")
    if prepared.source_sha256 != restore.get("source_sequence_sha256"):
        raise ScreenError("default restore source hash does not match manifest")
    actual_stream_hash = hashlib.sha256(prepared.stream).hexdigest()
    if (
        prepared.stream_sha256 != actual_stream_hash
        or actual_stream_hash != restore.get("prepared_stream_sha256")
    ):
        raise ScreenError("default restore stream hash does not match manifest")
    geometry = validate_stream(prepared.stream)
    if (
        restore.get("prepared_page_count") != 2009
        or prepared.page_count != 2009
        or geometry.frame_count != 251
        or geometry.page_count != 2009
    ):
        raise ScreenError("default restore page count must be exactly 2009")


def rgb565le(red: int, green: int, blue: int) -> bytes:
    """Encode one 8-bit RGB pixel as little-endian RGB565."""

    for name, value in (("red", red), ("green", green), ("blue", blue)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ScreenError(f"{name} must be an integer from 0 through 255")
    packed = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    return bytes((packed & 0xFF, packed >> 8))


def encode_rgb565le(image: Any) -> bytes:
    """Encode a Pillow image in row-major RGB565LE order."""

    if getattr(image, "size", None) != (LCD_WIDTH, LCD_HEIGHT):
        raise ScreenError(f"frame must be exactly {LCD_WIDTH}x{LCD_HEIGHT}")
    rgb = image.convert("RGB")
    output = bytearray(RGB565_FRAME_BYTES)
    offset = 0
    for red, green, blue in rgb.getdata():
        packed = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        output[offset] = packed & 0xFF
        output[offset + 1] = packed >> 8
        offset += 2
    return bytes(output)


def fit_image(image: Any, fit: str = "contain") -> Any:
    """Return a black-backed 128x128 RGB Pillow image."""

    Image, ImageOps, _ = _pillow()
    mode = _validate_fit(fit)
    rgba = ImageOps.exif_transpose(image).convert("RGBA")
    size = (LCD_WIDTH, LCD_HEIGHT)
    resampling = getattr(Image, "Resampling", Image).LANCZOS

    if mode == "contain":
        fitted = ImageOps.contain(rgba, size, method=resampling)
        placed = Image.new("RGBA", size, (0, 0, 0, 255))
        placed.alpha_composite(
            fitted,
            ((LCD_WIDTH - fitted.width) // 2, (LCD_HEIGHT - fitted.height) // 2),
        )
    elif mode == "cover":
        placed = ImageOps.fit(rgba, size, method=resampling, centering=(0.5, 0.5))
        background = Image.new("RGBA", size, (0, 0, 0, 255))
        background.alpha_composite(placed)
        placed = background
    else:
        placed = rgba.resize(size, resample=resampling)
        background = Image.new("RGBA", size, (0, 0, 0, 255))
        background.alpha_composite(placed)
        placed = background

    return placed.convert("RGB")


def build_stream(
    frames: Sequence[bytes], delays: Sequence[int]
) -> tuple[bytes, int, int, str, str]:
    """Build the delay-header/frame payload and page-aligned stream.

    Returns ``(stream, unpadded_bytes, page_count, payload_sha256,
    stream_sha256)``. Header byte 0 is the frame count, bytes 1..N are delays,
    and the unused header bytes are zero, matching the vendor image format.
    """

    frame_count = len(frames)
    if not MIN_FRAMES <= frame_count <= MAX_FRAMES:
        raise ScreenError("frame count must be from 1 through 255")
    if len(delays) != frame_count:
        raise ScreenError("one delay byte is required for every frame")

    header = bytearray(DELAY_HEADER_BYTES)
    header[0] = frame_count
    for index, delay in enumerate(delays):
        if isinstance(delay, bool) or not isinstance(delay, int) or not 1 <= delay <= 255:
            raise ScreenError(f"delay {index} must be an integer from 1 through 255")
        header[index + 1] = delay

    for index, frame in enumerate(frames):
        if len(frame) != RGB565_FRAME_BYTES:
            raise ScreenError(
                f"frame {index} must be {RGB565_FRAME_BYTES} bytes; got {len(frame)}"
            )

    payload = bytes(header) + b"".join(frames)
    padding = (-len(payload)) % LCD_PAGE_BYTES
    stream = payload + bytes(padding)
    validate_stream(stream)
    return (
        stream,
        len(payload),
        len(stream) // LCD_PAGE_BYTES,
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(stream).hexdigest(),
    )


def prepare_image(
    source: str | Path,
    *,
    fit: str = "contain",
    max_frames: int = MAX_FRAMES,
) -> PreparedScreen:
    """Decode and prepare a supported image file for the F75 Max LCD."""

    Image, _, UnidentifiedImageError = _pillow()
    fit = _validate_fit(fit)
    max_frames = _validate_frame_limit(max_frames)
    path = Path(source)
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise ScreenError(f"cannot read LCD source {path}: {exc}") from exc

    try:
        image = Image.open(path)
    except (UnidentifiedImageError, OSError) as exc:
        raise ScreenError(f"unsupported or invalid LCD image: {path}") from exc

    with image:
        source_format = (image.format or "").upper()
        if source_format not in SUPPORTED_FORMATS:
            raise ScreenError(
                f"unsupported LCD format {source_format or 'unknown'}; "
                f"expected {', '.join(sorted(SUPPORTED_FORMATS))}"
            )
        source_size = tuple(image.size)
        source_frame_count = int(getattr(image, "n_frames", 1) or 1)
        if source_frame_count < 1:
            raise ScreenError("image contains no frames")
        frame_count = min(source_frame_count, max_frames)
        encoded_frames: list[bytes] = []
        delays: list[int] = []

        for index in range(frame_count):
            image.seek(index)
            frame = image.convert("RGBA").copy()
            encoded_frames.append(encode_rgb565le(fit_image(frame, fit)))
            if source_frame_count == 1:
                delays.append(STILL_DELAY)
            else:
                delays.append(delay_byte(image.info.get("duration", frame.info.get("duration"))))

    stream, unpadded, page_count, payload_hash, stream_hash = build_stream(
        encoded_frames, delays
    )
    return PreparedScreen(
        source_name=path.name,
        source_format=source_format,
        source_size=(int(source_size[0]), int(source_size[1])),
        source_frame_count=source_frame_count,
        frame_count=frame_count,
        delays=tuple(delays),
        fit=fit,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        payload_sha256=payload_hash,
        stream_sha256=stream_hash,
        unpadded_bytes=unpadded,
        page_count=page_count,
        stream=stream,
    )


def prepare_frame_sequence(
    directory: str | Path,
    *,
    delay: int,
    fit: str = "stretch",
    max_frames: int = MAX_FRAMES,
) -> PreparedScreen:
    """Prepare numbered PNG frames with one explicit candidate wire delay byte.

    This is the lossless restore path for cached vendor profiles. GIF graphic
    control extensions can omit or zero frame durations even when the vendor
    database stores a nonzero playback delay, so restore never infers it.
    """

    Image, _, UnidentifiedImageError = _pillow()
    fit = _validate_fit(fit)
    max_frames = _validate_frame_limit(max_frames)
    if isinstance(delay, bool) or not isinstance(delay, int) or not 1 <= delay <= 255:
        raise ScreenError("sequence delay must be an integer from 1 through 255")
    root = Path(directory)
    if not root.is_dir():
        raise ScreenError(f"frame sequence directory not found: {root}")
    numbered: list[tuple[int, Path]] = []
    for path in root.glob("*.png"):
        try:
            index = int(path.stem)
        except ValueError as exc:
            raise ScreenError(f"sequence frame must have a numeric filename: {path}") from exc
        numbered.append((index, path))
    numbered.sort(key=lambda item: item[0])
    if not numbered:
        raise ScreenError(f"frame sequence contains no PNG files: {root}")
    expected = list(range(len(numbered)))
    actual = [index for index, _ in numbered]
    if actual != expected:
        raise ScreenError(
            f"frame sequence must be contiguous 0..{len(numbered) - 1}; got {actual[:8]}"
        )
    selected = numbered[:max_frames]
    source_hasher = hashlib.sha256()
    frames: list[bytes] = []
    source_size: tuple[int, int] | None = None
    for index, path in selected:
        source_bytes = path.read_bytes()
        source_hasher.update(index.to_bytes(2, "little"))
        source_hasher.update(hashlib.sha256(source_bytes).digest())
        try:
            image = Image.open(path)
        except (UnidentifiedImageError, OSError) as exc:
            raise ScreenError(f"invalid sequence frame: {path}") from exc
        with image:
            if (image.format or "").upper() != "PNG":
                raise ScreenError(f"restore sequence frame must be PNG: {path}")
            if source_size is None:
                source_size = (int(image.width), int(image.height))
            elif source_size != (image.width, image.height):
                raise ScreenError("all sequence frames must have the same dimensions")
            frames.append(encode_rgb565le(fit_image(image, fit)))

    delays = [delay] * len(frames)
    stream, unpadded, page_count, payload_hash, stream_hash = build_stream(
        frames, delays
    )
    return PreparedScreen(
        source_name=root.name,
        source_format="PNG_SEQUENCE",
        source_size=source_size or (0, 0),
        source_frame_count=len(numbered),
        frame_count=len(frames),
        delays=tuple(delays),
        fit=fit,
        source_sha256=source_hasher.hexdigest(),
        payload_sha256=payload_hash,
        stream_sha256=stream_hash,
        unpadded_bytes=unpadded,
        page_count=page_count,
        stream=stream,
    )


def prepare_test_pattern() -> PreparedScreen:
    """Create a deterministic high-contrast LCD alignment pattern."""

    Image, _, _ = _pillow()
    image = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (0, 0, 0))
    pixels = image.load()
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    for y in range(LCD_HEIGHT):
        for x in range(LCD_WIDTH):
            quadrant = (2 if y >= 64 else 0) + (1 if x >= 64 else 0)
            color = colors[quadrant]
            if x in (0, 63, 64, 127) or y in (0, 63, 64, 127) or x == y:
                color = (255, 255, 0)
            pixels[x, y] = color

    encoded = encode_rgb565le(image)
    stream, unpadded, page_count, payload_hash, stream_hash = build_stream(
        [encoded], [STILL_DELAY]
    )
    logical_source = image.tobytes()
    return PreparedScreen(
        source_name="f75max-test-pattern",
        source_format="GENERATED_RGB",
        source_size=(LCD_WIDTH, LCD_HEIGHT),
        source_frame_count=1,
        frame_count=1,
        delays=(STILL_DELAY,),
        fit="stretch",
        source_sha256=hashlib.sha256(logical_source).hexdigest(),
        payload_sha256=payload_hash,
        stream_sha256=stream_hash,
        unpadded_bytes=unpadded,
        page_count=page_count,
        stream=stream,
    )


def write_prepared(prepared: PreparedScreen, destination: str | Path) -> Path:
    """Write a prepared stream; intended for ignored local asset directories."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(prepared.stream)
    return path

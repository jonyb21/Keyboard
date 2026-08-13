"""Gate tests for deterministic F75 Max LCD preparation."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from services.hid import screen


class TestPixelEncoding(unittest.TestCase):
    def test_rgb565le_primaries(self):
        self.assertEqual(screen.rgb565le(255, 0, 0), b"\x00\xf8")
        self.assertEqual(screen.rgb565le(0, 255, 0), b"\xe0\x07")
        self.assertEqual(screen.rgb565le(0, 0, 255), b"\x1f\x00")
        self.assertEqual(screen.rgb565le(255, 255, 255), b"\xff\xff")
        self.assertEqual(screen.rgb565le(0, 0, 0), b"\x00\x00")

    def test_rgb565_is_row_major(self):
        from PIL import Image

        image = Image.new("RGB", (128, 128), (0, 0, 0))
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((1, 0), (0, 255, 0))
        image.putpixel((0, 1), (0, 0, 255))
        encoded = screen.encode_rgb565le(image)
        self.assertEqual(encoded[:4], b"\x00\xf8\xe0\x07")
        self.assertEqual(encoded[256:258], b"\x1f\x00")


class TestFitGeometry(unittest.TestCase):
    def test_contain_centers_with_black_bars(self):
        from PIL import Image

        source = Image.new("RGB", (200, 100), (255, 0, 0))
        fitted = screen.fit_image(source, "contain")
        self.assertEqual(fitted.getpixel((64, 10)), (0, 0, 0))
        self.assertEqual(fitted.getpixel((64, 64)), (255, 0, 0))
        self.assertEqual(fitted.getpixel((64, 117)), (0, 0, 0))

    def test_cover_crops_center(self):
        from PIL import Image

        source = Image.new("RGB", (256, 128), (255, 0, 0))
        for y in range(128):
            for x in range(96, 160):
                source.putpixel((x, y), (0, 255, 0))
        fitted = screen.fit_image(source, "cover")
        self.assertEqual(fitted.getpixel((64, 64)), (0, 255, 0))
        self.assertEqual(fitted.size, (128, 128))

    def test_stretch_uses_full_canvas(self):
        from PIL import Image

        source = Image.new("RGB", (1, 2), (0, 0, 255))
        fitted = screen.fit_image(source, "stretch")
        self.assertEqual(fitted.size, (128, 128))
        self.assertEqual(fitted.getpixel((127, 127)), (0, 0, 255))


class TestDelayAndBounds(unittest.TestCase):
    def test_delay_conversion_rounds_and_clamps(self):
        self.assertEqual(screen.delay_byte(None), 5)
        self.assertEqual(screen.delay_byte(-50), 5)
        self.assertEqual(screen.delay_byte(0), 5)
        self.assertEqual(screen.delay_byte(1), 1)
        self.assertEqual(screen.delay_byte(3), 2)
        self.assertEqual(screen.delay_byte(30), 15)
        self.assertEqual(screen.delay_byte(500), 250)
        self.assertEqual(screen.delay_byte(9999), 255)

    def test_frame_count_and_delay_bounds(self):
        frame = bytes(screen.RGB565_FRAME_BYTES)
        with self.assertRaises(screen.ScreenError):
            screen.build_stream([], [])
        with self.assertRaises(screen.ScreenError):
            screen.build_stream([frame] * 256, [1] * 256)
        with self.assertRaises(screen.ScreenError):
            screen.build_stream([frame], [0])
        with self.assertRaises(screen.ScreenError):
            screen.build_stream([frame], [256])


class TestStreamGeometry(unittest.TestCase):
    def test_header_page_counts_and_zero_filler_at_1_251_255_frames(self):
        frame = bytes(screen.RGB565_FRAME_BYTES)
        for count in (1, 251, 255):
            with self.subTest(count=count):
                delays = [15] * count
                stream, unpadded, pages, _, _ = screen.build_stream(
                    [frame] * count, delays
                )
                self.assertEqual(stream[0], count)
                self.assertEqual(stream[1 : 1 + count], bytes(delays))
                self.assertEqual(
                    stream[1 + count : screen.DELAY_HEADER_BYTES],
                    bytes(screen.DELAY_HEADER_BYTES - 1 - count),
                )
                self.assertEqual(unpadded, 256 + count * 32768)
                self.assertEqual(pages, 1 + count * 8)
                self.assertEqual(len(stream), pages * 4096)
                self.assertEqual(stream[unpadded:], bytes(len(stream) - unpadded))

    def test_pages_are_exactly_4096_bytes(self):
        prepared = screen.prepare_test_pattern()
        self.assertEqual(len(prepared.pages()), 9)
        self.assertTrue(all(len(page) == 4096 for page in prepared.pages()))

    def test_validator_accepts_exact_1_and_255_frame_geometry(self):
        frame = bytes(screen.RGB565_FRAME_BYTES)
        for count in (1, 255):
            with self.subTest(count=count):
                stream, *_ = screen.build_stream([frame] * count, [5] * count)
                geometry = screen.validate_stream(stream)
                self.assertEqual(geometry.frame_count, count)
                self.assertEqual(geometry.page_count, 1 + 8 * count)

    def test_validator_rejects_each_malformed_stream_field(self):
        valid = bytearray(
            screen.build_stream([bytes(screen.RGB565_FRAME_BYTES)], [5])[0]
        )
        cases = {}
        zero_count = bytearray(valid)
        zero_count[0] = 0
        cases["frame count"] = bytes(zero_count)
        zero_delay = bytearray(valid)
        zero_delay[1] = 0
        cases["delay"] = bytes(zero_delay)
        nonzero_filler = bytearray(valid)
        nonzero_filler[2] = 1
        cases["header filler"] = bytes(nonzero_filler)
        cases["page count"] = bytes(valid) + bytes(screen.LCD_PAGE_BYTES)
        nonzero_tail = bytearray(valid)
        nonzero_tail[-1] = 1
        cases["tail padding"] = bytes(nonzero_tail)
        for message, candidate in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(screen.ScreenError, message):
                    screen.validate_stream(candidate)


class TestRestoreManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = bytes(screen.RGB565_FRAME_BYTES)
        stream, unpadded, pages, payload_hash, stream_hash = screen.build_stream(
            [frame] * 251, [10] * 251
        )
        cls._prepared = screen.PreparedScreen(
            source_name="frames",
            source_format="PNG_SEQUENCE",
            source_size=(128, 128),
            source_frame_count=251,
            frame_count=251,
            delays=(10,) * 251,
            fit="stretch",
            source_sha256="a" * 64,
            payload_sha256=payload_hash,
            stream_sha256=stream_hash,
            unpadded_bytes=unpadded,
            page_count=pages,
            stream=stream,
        )

    def manifest(self):
        return {
            "schema_version": 1,
            "restore": {
                "source_size": [128, 128],
                "frame_count": 251,
                "candidate_delay_byte": 10,
                "source_sequence_sha256": "a" * 64,
                "prepared_stream_sha256": self._prepared.stream_sha256,
                "prepared_page_count": 2009,
            },
        }

    def prepared(self):
        return self._prepared

    def test_manifest_accepts_all_exact_restore_evidence(self):
        screen.validate_restore_manifest(self.prepared(), self.manifest(), 10)

    def test_manifest_fails_closed_on_each_required_mismatch(self):
        prepared = self.prepared()
        mutations = (
            ("dimensions", replace(prepared, source_size=(64, 128)), self.manifest(), 10),
            ("251", replace(prepared, frame_count=250), self.manifest(), 10),
            ("source hash", replace(prepared, source_sha256="c" * 64), self.manifest(), 10),
            ("stream hash", replace(prepared, stream_sha256="c" * 64), self.manifest(), 10),
            ("page count", replace(prepared, page_count=2008), self.manifest(), 10),
            ("candidate delay", prepared, self.manifest(), 9),
        )
        for message, candidate, manifest, delay in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(screen.ScreenError, message):
                    screen.validate_restore_manifest(candidate, manifest, delay)


class TestImageDecoding(unittest.TestCase):
    def test_still_png_uses_delay_255_and_hashes(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "still.png")
            Image.new("RGB", (32, 16), (255, 0, 0)).save(path)
            prepared = screen.prepare_image(path, fit="contain")

        self.assertEqual(prepared.source_format, "PNG")
        self.assertEqual(prepared.source_size, (32, 16))
        self.assertEqual(prepared.frame_count, 1)
        self.assertEqual(prepared.delays, (255,))
        self.assertEqual(prepared.page_count, 9)
        self.assertEqual(len(prepared.source_sha256), 64)
        self.assertEqual(len(prepared.payload_sha256), 64)
        self.assertEqual(len(prepared.stream_sha256), 64)
        self.assertEqual(prepared.metadata()["stream_bytes"], 9 * 4096)

    def test_animated_gif_delays_and_frame_limit(self):
        from PIL import Image

        frames = [
            Image.new("RGB", (8, 8), (255, 0, 0)),
            Image.new("RGB", (8, 8), (0, 255, 0)),
            Image.new("RGB", (8, 8), (0, 0, 255)),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "animation.gif")
            frames[0].save(
                path,
                save_all=True,
                append_images=frames[1:],
                duration=[30, 100, 500],
                loop=0,
            )
            prepared = screen.prepare_image(path, fit="stretch", max_frames=2)

        self.assertEqual(prepared.source_frame_count, 3)
        self.assertEqual(prepared.frame_count, 2)
        self.assertEqual(prepared.delays, (15, 50))

    def test_animated_gif_missing_duration_uses_exact_source_fallback(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "missing-duration.gif")
            frames = (
                Image.new("RGB", (8, 8), (255, 0, 0)),
                Image.new("RGB", (8, 8), (0, 0, 255)),
            )
            frames[0].save(
                path,
                format="GIF",
                save_all=True,
                append_images=[frames[1]],
                loop=0,
            )
            prepared = screen.prepare_image(path)

        self.assertEqual(prepared.frame_count, 2)
        self.assertEqual(prepared.delays, (5, 5))
        self.assertEqual(prepared.page_count, 17)

    def test_all_required_formats_decode_when_pillow_supports_them(self):
        from PIL import Image, features

        formats = {
            "PNG": "png",
            "JPEG": "jpg",
            "BMP": "bmp",
            "TIFF": "tiff",
            "WEBP": "webp",
        }
        with tempfile.TemporaryDirectory() as directory:
            for expected, extension in formats.items():
                if expected == "WEBP" and not features.check("webp"):
                    continue
                with self.subTest(format=expected):
                    path = Path(directory, f"source.{extension}")
                    Image.new("RGB", (4, 4), (12, 34, 56)).save(path)
                    self.assertEqual(screen.prepare_image(path).source_format, expected)

    def test_invalid_fit_and_frame_limit_are_rejected(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "still.png")
            Image.new("RGB", (4, 4)).save(path)
            with self.assertRaises(screen.ScreenError):
                screen.prepare_image(path, fit="letterbox-ish")
            with self.assertRaises(screen.ScreenError):
                screen.prepare_image(path, max_frames=0)

    def test_numbered_restore_sequence_preserves_order_and_explicit_delay(self):
        from PIL import Image

        colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
        with tempfile.TemporaryDirectory() as directory:
            # Write out of order to prove numeric sorting, not directory order.
            for index in (2, 0, 1):
                Image.new("RGB", (128, 128), colors[index]).save(
                    Path(directory, f"{index}.png")
                )
            prepared = screen.prepare_frame_sequence(directory, delay=10)

        self.assertEqual(prepared.source_format, "PNG_SEQUENCE")
        self.assertEqual(prepared.frame_count, 3)
        self.assertEqual(prepared.delays, (10, 10, 10))
        self.assertEqual(prepared.stream[0], 3)
        self.assertEqual(prepared.stream[1:4], b"\x0a\x0a\x0a")
        first = screen.DELAY_HEADER_BYTES
        second = first + screen.RGB565_FRAME_BYTES
        third = second + screen.RGB565_FRAME_BYTES
        self.assertEqual(prepared.stream[first : first + 2], b"\x00\xf8")
        self.assertEqual(prepared.stream[second : second + 2], b"\xe0\x07")
        self.assertEqual(prepared.stream[third : third + 2], b"\x1f\x00")

    def test_restore_sequence_rejects_gaps_and_bad_delay(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            Image.new("RGB", (8, 8)).save(Path(directory, "0.png"))
            Image.new("RGB", (8, 8)).save(Path(directory, "2.png"))
            with self.assertRaisesRegex(screen.ScreenError, "contiguous"):
                screen.prepare_frame_sequence(directory, delay=10)
            with self.assertRaisesRegex(screen.ScreenError, "1 through 255"):
                screen.prepare_frame_sequence(directory, delay=0)


if __name__ == "__main__":
    unittest.main()

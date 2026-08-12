"""Gate tests for services.hid.device: discovery filtering on fake
enumerations. No device is ever opened."""

import unittest

from services.hid import device


def rec(
    vid,
    pid,
    usage_page,
    usage,
    interface,
    path="p",
    product_string=None,
    release_number=None,
):
    if product_string is None:
        product_string = (
            device.WIRED_PRODUCT_STRING
            if (vid, pid) == (device.WIRED_VID, device.WIRED_PID)
            else "2.4G Dongle"
        )
    if release_number is None:
        release_number = (
            device.WIRED_RELEASE_NUMBER
            if (vid, pid) == (device.WIRED_VID, device.WIRED_PID)
            else 0
        )
    return {
        "vendor_id": vid,
        "product_id": pid,
        "usage_page": usage_page,
        "usage": usage,
        "interface_number": interface,
        "path": path,
        "product_string": product_string,
        "release_number": release_number,
    }


# Fake wired-mode enumeration mirroring the registry interface map in
# Exact vendor v1.0.0.5 config.xml and live Windows endpoint inventory.
WIRED_ENUM = [
    rec(0x0C45, 0x800A, 0x0001, 0x0006, 0, path=b"\\\\?\\hid#mi_00"),  # boot kbd
    rec(0x0C45, 0x800A, 0x000C, 0x0001, 1, path="mi_01_col01"),  # consumer
    rec(0x0C45, 0x800A, 0x0001, 0x0080, 1, path="mi_01_col02"),  # system
    rec(0x0C45, 0x800A, 0x0001, 0x0006, 1, path="mi_01_col03"),  # nkro
    rec(0x0C45, 0x800A, 0x0001, 0x0002, 1, path="mi_01_col04"),  # mouse
    rec(0x0C45, 0x800A, 0xFFFF, 0x0001, 1, path="mi_01_col05"),  # vendor
    rec(0x0C45, 0x800A, 0xFF68, 0x0061, 2, path="mi_02_screen"),
    rec(0x0C45, 0x800A, 0xFF13, 0x0001, 3, path="mi_03_config"),
]

DONGLE_ENUM = [
    rec(0x05AC, 0x024F, 0x0001, 0x0006, 0, path="d_mi_00"),
    rec(0x05AC, 0x024F, 0x000C, 0x0001, 1, path="d_mi_01"),
    rec(0x05AC, 0x024F, 0xFF60, 0x0062, 3, path="d_wrong_usage"),
    rec(0x05AC, 0x024F, 0xFF60, 0x0061, 3, path="d_mi_03_raw"),
]

NOISE = [
    rec(0x046D, 0xC08B, 0x0001, 0x0002, 0, path="logitech_mouse"),
    rec(0x0C45, 0x7903, 0xFF13, 0x0001, 3, path="other_sonix_pid"),
    rec(0x05AC, 0x0250, 0xFF60, 0x0061, 3, path="other_apple_pid"),
]


class TestClassify(unittest.TestCase):
    def test_wired_config_by_usage_page(self):
        self.assertEqual(
            device.classify(rec(0x0C45, 0x800A, 0xFF13, 0x0001, 3)),
            device.KIND_WIRED_CONFIG,
        )

    def test_wired_screen_by_usage_page(self):
        self.assertEqual(
            device.classify(rec(0x0C45, 0x800A, 0xFF68, 0x0061, 2)),
            device.KIND_WIRED_SCREEN,
        )

    def test_dongle_requires_usage_0x61(self):
        self.assertEqual(
            device.classify(rec(0x05AC, 0x024F, 0xFF60, 0x0061, 3)),
            device.KIND_DONGLE_CONFIG,
        )
        self.assertIsNone(device.classify(rec(0x05AC, 0x024F, 0xFF60, 0x0062, 3)))

    def test_boot_keyboard_collection_is_not_config(self):
        # Vendor config.xml names MI_00, but the feature collection is 0xFF13
        # (conflict C2 in contracts/hid_protocol.md).
        self.assertIsNone(device.classify(rec(0x0C45, 0x800A, 0x0001, 0x0006, 0)))

    def test_foreign_devices_ignored(self):
        for record in NOISE:
            self.assertIsNone(device.classify(record), record["path"])

    def test_interface_fallback_when_usage_page_missing(self):
        # hidraw-style records report usage_page 0.
        self.assertEqual(
            device.classify(rec(0x0C45, 0x800A, 0, 0, 3)),
            device.KIND_WIRED_CONFIG,
        )
        self.assertEqual(
            device.classify(rec(0x0C45, 0x800A, 0, 0, 2)),
            device.KIND_WIRED_SCREEN,
        )
        self.assertEqual(
            device.classify(rec(0x05AC, 0x024F, 0, 0, 3)),
            device.KIND_DONGLE_CONFIG,
        )
        self.assertIsNone(device.classify(rec(0x0C45, 0x800A, 0, 0, 0)))

    def test_missing_fields_tolerated(self):
        self.assertIsNone(device.classify({"vendor_id": 0x0C45}))
        self.assertIsNone(device.classify({}))


class TestDiscover(unittest.TestCase):
    def test_full_wired_plus_dongle(self):
        found = device.discover(NOISE + WIRED_ENUM + DONGLE_ENUM)
        self.assertIsNotNone(found.wired_config)
        self.assertEqual(found.wired_config.path, "mi_03_config")
        self.assertEqual(found.wired_config.interface_number, 3)
        self.assertEqual(found.wired_screen.path, "mi_02_screen")
        self.assertEqual(found.dongle_config.path, "d_mi_03_raw")

    def test_preferred_config_is_wired_first(self):
        both = device.discover(WIRED_ENUM + DONGLE_ENUM)
        self.assertEqual(both.preferred_config.kind, device.KIND_WIRED_CONFIG)
        dongle_only = device.discover(DONGLE_ENUM)
        self.assertEqual(
            dongle_only.preferred_config.kind, device.KIND_DONGLE_CONFIG
        )
        self.assertIsNone(device.discover(NOISE).preferred_config)

    def test_first_match_wins(self):
        dup = rec(0x0C45, 0x800A, 0xFF13, 0x0001, 3, path="second_config")
        found = device.discover(WIRED_ENUM + [dup])
        self.assertEqual(found.wired_config.path, "mi_03_config")

    def test_bytes_paths_decoded(self):
        found = device.discover(
            [rec(0x0C45, 0x800A, 0xFF13, 0x0001, 3, path=b"\\\\?\\hid#bytes")]
        )
        self.assertEqual(found.wired_config.path, "\\\\?\\hid#bytes")

    def test_endpoint_kind_flags(self):
        found = device.discover(WIRED_ENUM + DONGLE_ENUM)
        self.assertTrue(found.wired_config.is_wired)
        self.assertTrue(found.wired_screen.is_wired)
        self.assertFalse(found.dongle_config.is_wired)

    def test_exact_wired_identity_requires_product_and_revision(self):
        exact = device.discover(WIRED_ENUM).wired_config
        self.assertTrue(exact.is_exact_wired_target)

        wrong_product = device.discover(
            [
                rec(
                    0x0C45,
                    0x800A,
                    0xFF13,
                    0x0001,
                    3,
                    product_string="AULA F108Pro",
                )
            ]
        ).wired_config
        wrong_revision = device.discover(
            [
                rec(
                    0x0C45,
                    0x800A,
                    0xFF13,
                    0x0001,
                    3,
                    release_number=0x0107,
                )
            ]
        ).wired_config
        self.assertFalse(wrong_product.is_exact_wired_target)
        self.assertFalse(wrong_revision.is_exact_wired_target)

    def test_exact_wired_identity_wins_over_shared_pid_first_match(self):
        shared = rec(
            0x0C45,
            0x800A,
            0xFF13,
            0x0001,
            3,
            path="shared_pid",
            product_string="AULA F108Pro",
        )
        exact = rec(
            0x0C45,
            0x800A,
            0xFF13,
            0x0001,
            3,
            path="exact_f75max",
        )
        found = device.discover([shared, exact])
        self.assertEqual(found.wired_config.path, "exact_f75max")
        self.assertTrue(found.wired_config.is_exact_wired_target)


if __name__ == "__main__":
    unittest.main()

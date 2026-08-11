"""Gate tests: the tap-hold discriminator.

Drives mirror.TapHold through mirror.FakeEngine with a fake integer clock, so
every timing assertion is exact and the suite never sleeps.

The state machine under test is a straight port of core.ahk's HL_TapHold; the
step labels TH-L1..TH-L8 appear in both files.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror  # noqa: E402

THRESHOLD = 200


def engine_cfg(**overrides):
    cfg = {
        "schemaVersion": 1,
        "tapHoldMs": THRESHOLD,
        "appGroups": {"solidworks": ["SLDWORKS.exe"], "excel": ["EXCEL.EXE"]},
        "bindings": {
            # tap + hold, plain chords
            "F22": {"tap": {"send": "Ctrl+S"}, "hold": {"send": "Ctrl+Shift+S"}},
            # tap + hold, app-aware hold
            "F13": {
                "tap": {"send": "Ctrl+Z"},
                "hold": {
                    "byApp": {
                        "solidworks": {"send": "Ctrl+Y"},
                        "excel": {"send": "Ctrl+Y"},
                        "default": {"send": "Ctrl+Shift+Z"},
                    }
                },
            },
            # tap-only
            "F19": {"tap": {"layerToggle": "num"}},
            # builtin switcher
            "F15": {"tap": {"builtin": "altTabTap"}, "hold": {"builtin": "altTabBrowse"}},
            # focus-or-launch pair
            "F17": {
                "tap": {"focusApp": {"exe": "InDesign (Beta).exe"}},
                "hold": {"focusApp": {"exe": "Photoshop.exe"}},
            },
            # hold that deliberately emits nothing
            "F20": {"tap": {"send": "Ctrl+V"}, "hold": {"swallow": True}},
        },
        "layers": {"num": {"type": "toggle", "keys": {"u": "7"}}},
    }
    cfg.update(overrides)
    return mirror.load_config_obj(cfg)


def new_engine(active_exe=""):
    return mirror.FakeEngine(engine_cfg(), active_exe=active_exe)


class TestTapBelowThreshold(unittest.TestCase):
    def test_tap_fires_on_release_not_on_press(self):
        e = new_engine()
        e.press("F22")
        self.assertEqual([], e.drain(), "nothing may be emitted on key-down")
        e.advance(50)
        e.release("F22")
        self.assertEqual(["send:^{s}"], e.drain())

    def test_tap_just_under_threshold_is_still_a_tap(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD - 1)
        e.release("F22")
        self.assertEqual(["send:^{s}"], e.drain())

    def test_tap_cancels_the_pending_hold(self):
        e = new_engine()
        e.press("F22")
        e.advance(50)
        e.release("F22")
        e.drain()
        e.advance(1000)
        self.assertEqual([], e.drain(), "a cancelled hold must never fire late")

    def test_state_returns_to_idle_after_a_tap(self):
        e = new_engine()
        e.tap("F22")
        self.assertEqual("idle", e.machine.phase("f22"))

    def test_repeated_taps_each_fire_once(self):
        e = new_engine()
        for _ in range(3):
            e.tap("F22", ms=20)
        self.assertEqual(["send:^{s}"] * 3, e.drain())


class TestHoldPastThreshold(unittest.TestCase):
    def test_hold_fires_exactly_at_the_threshold(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD)
        self.assertEqual(["send:^+{s}"], e.drain())

    def test_hold_fires_before_release(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD + 500)
        self.assertEqual(["send:^+{s}"], e.drain())
        e.release("F22")
        self.assertEqual([], e.drain(), "release after a hold emits nothing")

    def test_hold_fires_once_no_matter_how_long_it_is_held(self):
        e = new_engine()
        e.press("F22")
        e.advance(5000)
        e.release("F22")
        self.assertEqual(["send:^+{s}"], e.drain())

    def test_tap_never_fires_after_a_hold(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD + 10)
        e.release("F22")
        self.assertNotIn("send:^{s}", e.drain())

    def test_hold_may_emit_nothing(self):
        e = new_engine()
        e.press("F20")
        e.advance(THRESHOLD)
        e.release("F20")
        self.assertEqual(["swallow"], e.drain())


class TestAutorepeatSafety(unittest.TestCase):
    """Correctness rule 2: holding a key past the threshold must not autorepeat
    the tap action. Windows delivers repeated key-down events with no key-up."""

    def test_autorepeat_while_pending_is_swallowed(self):
        e = new_engine()
        e.press("F22")
        for _ in range(8):
            e.advance(15)
            e.press("F22")          # OS autorepeat, still under threshold
        self.assertEqual([], e.drain())

    def test_autorepeat_after_hold_does_not_refire_the_hold(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD)
        e.drain()
        for _ in range(10):
            e.advance(30)
            e.press("F22")
        self.assertEqual([], e.drain())

    def test_autorepeat_of_a_tap_only_key_fires_once(self):
        e = new_engine()
        e.press("F19")
        self.assertEqual(["layer:num=on"], e.drain())
        for _ in range(10):
            e.advance(30)
            e.press("F19")
        self.assertEqual([], e.drain(), "a sticky toggle must not flap on autorepeat")
        e.release("F19")
        self.assertEqual([], e.drain())

    def test_a_full_press_release_cycle_toggles_again(self):
        e = new_engine()
        e.tap("F19")
        e.tap("F19")
        self.assertEqual(["layer:num=on", "layer:num=off"], e.drain())


class TestTapOnlyKeys(unittest.TestCase):
    """A binding with no hold has nothing to discriminate, so it must fire the
    instant the key goes down: zero added latency."""

    def test_tap_only_fires_on_key_down(self):
        e = new_engine()
        e.press("F19")
        self.assertEqual(["layer:num=on"], e.drain())

    def test_tap_only_arms_no_timer(self):
        e = new_engine()
        e.press("F19")
        self.assertEqual({}, e.timers)

    def test_tap_only_release_emits_nothing(self):
        e = new_engine()
        e.press("F19")
        e.drain()
        e.advance(THRESHOLD + 100)
        e.release("F19")
        self.assertEqual([], e.drain())

    def test_tap_hold_key_arms_a_timer(self):
        e = new_engine()
        e.press("F22")
        self.assertEqual({"f22": THRESHOLD}, e.timers)


class TestAppAwareHold(unittest.TestCase):
    def test_solidworks_gets_ctrl_y(self):
        e = mirror.FakeEngine(engine_cfg(), active_exe="SLDWORKS.exe")
        e.press("F13")
        e.advance(THRESHOLD)
        self.assertEqual(["send:^{y}"], e.drain())

    def test_excel_gets_ctrl_y(self):
        e = mirror.FakeEngine(engine_cfg(), active_exe="EXCEL.EXE")
        e.press("F13")
        e.advance(THRESHOLD)
        self.assertEqual(["send:^{y}"], e.drain())

    def test_everything_else_gets_ctrl_shift_z(self):
        e = mirror.FakeEngine(engine_cfg(), active_exe="Photoshop.exe")
        e.press("F13")
        e.advance(THRESHOLD)
        self.assertEqual(["send:^+{z}"], e.drain())

    def test_tap_is_undo_everywhere(self):
        for exe in ("SLDWORKS.exe", "EXCEL.EXE", "chrome.exe", ""):
            e = mirror.FakeEngine(engine_cfg(), active_exe=exe)
            e.tap("F13")
            with self.subTest(exe=exe):
                self.assertEqual(["send:^{z}"], e.drain())


class TestFocusAppPairs(unittest.TestCase):
    def test_tap_focuses_indesign_hold_focuses_photoshop(self):
        e = new_engine()
        e.tap("F17")
        self.assertEqual(["focus:InDesign (Beta).exe"], e.drain())
        e.press("F17")
        e.advance(THRESHOLD)
        e.release("F17")
        self.assertEqual(["focus:Photoshop.exe"], e.drain())


class TestAltTabSwitcher(unittest.TestCase):
    def test_tap_is_a_plain_alt_tab(self):
        e = new_engine()
        e.tap("F15")
        self.assertEqual(["send:!{Tab}"], e.drain())

    def test_hold_opens_the_switcher_and_release_commits(self):
        e = new_engine()
        e.press("F15")
        e.advance(THRESHOLD)
        e.release("F15")
        self.assertEqual(["send:{Alt down}{Tab}", "send:{Alt up}"], e.drain())

    def test_full_browse_sequence(self):
        """Hold, then two further presses while held, then release."""
        e = new_engine()
        e.press("F15")
        e.advance(THRESHOLD)          # switcher opens on the first Tab
        e.advance(60)
        e.press("F15")                # advance one
        e.advance(60)
        e.press("F15")                # advance two
        e.release("F15")              # commit
        self.assertEqual(
            [
                "send:{Alt down}{Tab}",
                "send:{Tab}",
                "send:{Tab}",
                "send:{Alt up}",
            ],
            e.drain(),
        )

    def test_alt_is_not_left_stranded(self):
        e = new_engine()
        e.press("F15")
        e.advance(THRESHOLD + 100)
        e.press("F15")
        e.release("F15")
        self.assertFalse(e.alt_held, "Alt must be released when the hold ends")

    def test_a_tap_never_touches_the_alt_key_state(self):
        e = new_engine()
        e.tap("F15")
        self.assertFalse(e.alt_held)


class TestThresholdIsConfigurable(unittest.TestCase):
    def test_custom_threshold_moves_the_boundary(self):
        cfg = {
            "schemaVersion": 1,
            "tapHoldMs": 350,
            "bindings": {
                "F22": {"tap": {"send": "Ctrl+S"}, "hold": {"send": "Ctrl+Shift+S"}}
            },
            "layers": {"num": {"type": "toggle", "keys": {"u": "7"}}},
        }
        e = mirror.FakeEngine(mirror.load_config_obj(cfg))
        e.press("F22")
        e.advance(300)
        self.assertEqual([], e.drain(), "300ms is still a tap at a 350ms threshold")
        e.release("F22")
        self.assertEqual(["send:^{s}"], e.drain())

    def test_custom_threshold_hold(self):
        cfg = {
            "schemaVersion": 1,
            "tapHoldMs": 90,
            "bindings": {
                "F22": {"tap": {"send": "Ctrl+S"}, "hold": {"send": "Ctrl+Shift+S"}}
            },
            "layers": {"num": {"type": "toggle", "keys": {"u": "7"}}},
        }
        e = mirror.FakeEngine(mirror.load_config_obj(cfg))
        e.press("F22")
        e.advance(90)
        self.assertEqual(["send:^+{s}"], e.drain())


class TestIndependentKeys(unittest.TestCase):
    def test_two_keys_held_at_once_do_not_interfere(self):
        e = new_engine()
        e.press("F22")
        e.advance(20)
        e.press("F13")
        e.advance(THRESHOLD)
        # both cross the threshold, each fires its own hold once
        self.assertEqual(["send:^+{s}", "send:^+{z}"], e.drain())
        e.release("F22")
        e.release("F13")
        self.assertEqual([], e.drain())

    def test_one_key_tapped_while_another_is_held(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD)
        e.drain()
        e.tap("F13", ms=10)
        self.assertEqual(["send:^{z}"], e.drain())


class TestReset(unittest.TestCase):
    def test_reset_clears_every_phase(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD)
        e.drain()
        self.assertEqual("held", e.machine.phase("f22"))
        e.machine.reset()
        self.assertEqual("idle", e.machine.phase("f22"))

    def test_release_after_reset_emits_nothing(self):
        e = new_engine()
        e.press("F22")
        e.advance(THRESHOLD)
        e.drain()
        e.machine.reset()
        e.release("F22")
        self.assertEqual([], e.drain())


if __name__ == "__main__":
    unittest.main()

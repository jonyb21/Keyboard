"""Gate tests: multi-tap triggers, encoder press modes, reserved keys, config path.

Same approach as test_taphold.py: mirror.TapHold driven by mirror.FakeEngine on a
fake integer clock. TH-L9..TH-L12 in core.ahk are the steps under test.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror  # noqa: E402

HOLD = 200
WINDOW = 250
DEVICES = ["VID_0C45&PID_800A"]


def make_cfg(bindings=None, encoder=None, **root):
    cfg = {
        "schemaVersion": 1,
        "tapHoldMs": HOLD,
        "tapWindowMs": WINDOW,
        "appGroups": {},
        "bindings": bindings
        or {
            # tap-only: must keep firing on key-down
            "F13": {"tap": {"send": "Ctrl+Z"}},
            # tap + hold, no multi: must keep firing on key-up
            "F14": {"tap": {"send": "Ctrl+C"}, "hold": {"send": "Ctrl+X"}},
            # tap + double
            "F18": {"tap": {"send": "Ctrl+S"}, "doubleTap": {"send": "Ctrl+Shift+S"}},
            # all three, plus hold and tap-then-hold
            "F19": {
                "tap": {"send": "Ctrl+A"},
                "doubleTap": {"send": "Ctrl+B"},
                "tripleTap": {"send": "Ctrl+D"},
                "hold": {"send": "Ctrl+E"},
                "tapHold": {"send": "Ctrl+F"},
            },
            # triple without double
            "F20": {"tap": {"send": "Ctrl+G"}, "tripleTap": {"send": "Ctrl+H"}},
            # tap-then-hold only
            "F21": {"tap": {"send": "Ctrl+I"}, "tapHold": {"send": "Ctrl+J"}},
        },
        "layers": {"num": {"type": "toggle", "keys": {"u": "7"}}},
    }
    cfg.update(root)
    if encoder is not None:
        cfg["encoder"] = dict({"devices": DEVICES}, **encoder)
    return cfg


def eng(**kw):
    return mirror.FakeEngine(mirror.load_config_obj(make_cfg(**kw)))


class TestNoAddedLatency(unittest.TestCase):
    def test_tap_only_still_fires_on_key_down(self):
        e = eng()
        e.press("F13")
        self.assertEqual(["send:^{z}"], e.drain())
        self.assertEqual({}, e.timers)

    def test_tap_hold_without_multi_still_fires_on_key_up_with_no_wait(self):
        e = eng()
        e.press("F14")
        e.advance(30)
        e.release("F14")
        self.assertEqual(["send:^{c}"], e.drain())
        self.assertNotIn("wait:f14", e.timers)

    def test_hold_without_multi_unchanged(self):
        e = eng()
        e.press("F14")
        e.advance(HOLD)
        self.assertEqual(["send:^{x}"], e.drain())
        e.release("F14")
        self.assertEqual([], e.drain())


class TestDoubleTap(unittest.TestCase):
    def test_single_tap_waits_the_window_then_fires_tap(self):
        e = eng()
        e.tap("F18")
        self.assertEqual([], e.drain())
        e.advance(WINDOW - 1)
        self.assertEqual([], e.drain())
        e.advance(1)
        self.assertEqual(["send:^{s}"], e.drain())

    def test_second_tap_inside_window_fires_double_only(self):
        e = eng()
        e.tap("F18")
        e.advance(100)
        e.tap("F18")
        self.assertEqual(["send:^+{s}"], e.drain())
        e.advance(1000)
        self.assertEqual([], e.drain(), "the window timer must be dead")

    def test_second_tap_after_window_is_a_fresh_single(self):
        e = eng()
        e.tap("F18")
        e.advance(WINDOW)
        self.assertEqual(["send:^{s}"], e.drain())
        e.tap("F18")
        e.advance(WINDOW)
        self.assertEqual(["send:^{s}"], e.drain())

    def test_third_tap_without_triple_starts_a_new_sequence(self):
        e = eng()
        e.tap("F18")
        e.tap("F18")
        self.assertEqual(["send:^+{s}"], e.drain())
        e.tap("F18")
        e.advance(WINDOW)
        self.assertEqual(["send:^{s}"], e.drain())

    def test_autorepeat_while_down_is_swallowed(self):
        e = eng()
        e.press("F18")
        for _ in range(5):
            e.advance(30)
            e.press("F18")
        e.release("F18")
        e.advance(WINDOW)
        self.assertEqual(["send:^{s}"], e.drain())

    def test_long_press_with_no_hold_is_still_a_tap(self):
        e = eng()
        e.press("F18")
        e.advance(600)
        e.release("F18")
        e.advance(WINDOW)
        self.assertEqual(["send:^{s}"], e.drain())


class TestTripleTap(unittest.TestCase):
    def test_three_quick_taps_fire_triple_immediately_on_third_release(self):
        e = eng()
        e.tap("F19")
        e.advance(80)
        e.tap("F19")
        e.advance(80)
        e.press("F19")
        e.advance(10)
        self.assertEqual([], e.drain())
        e.release("F19")
        self.assertEqual(["send:^{d}"], e.drain())
        e.advance(1000)
        self.assertEqual([], e.drain())

    def test_two_taps_then_window_fires_double(self):
        e = eng()
        e.tap("F19")
        e.advance(80)
        e.tap("F19")
        self.assertEqual([], e.drain(), "a third tap is still possible")
        e.advance(WINDOW)
        self.assertEqual(["send:^{b}"], e.drain())

    def test_one_tap_then_window_fires_tap(self):
        e = eng()
        e.tap("F19")
        e.advance(WINDOW)
        self.assertEqual(["send:^{a}"], e.drain())

    def test_triple_without_double_two_taps_fall_back_to_tap_once(self):
        e = eng()
        e.tap("F20")
        e.tap("F20")
        e.advance(WINDOW)
        self.assertEqual(["send:^{g}"], e.drain())

    def test_triple_without_double_three_taps_fire_triple(self):
        e = eng()
        for _ in range(3):
            e.tap("F20")
        self.assertEqual(["send:^{h}"], e.drain())

    def test_any_action_kind_works_as_multi_tap(self):
        cfg = make_cfg(
            bindings={
                "F13": {
                    "tap": {"send": "Ctrl+Z"},
                    "doubleTap": {"layerToggle": "num"},
                    "tripleTap": {"swallow": True},
                }
            }
        )
        e = mirror.FakeEngine(mirror.load_config_obj(cfg))
        e.tap("F13")
        e.tap("F13")
        e.advance(WINDOW)
        self.assertEqual(["layer:num=on"], e.drain())


class TestHoldAndTapHold(unittest.TestCase):
    def test_hold_still_fires_at_threshold_on_first_press(self):
        e = eng()
        e.press("F19")
        e.advance(HOLD)
        self.assertEqual(["send:^{e}"], e.drain())
        e.release("F19")
        e.advance(1000)
        self.assertEqual([], e.drain())

    def test_tap_then_hold_fires_tap_hold_not_hold_or_tap(self):
        e = eng()
        e.tap("F19")
        e.advance(100)
        e.press("F19")
        e.advance(HOLD)
        self.assertEqual(["send:^{f}"], e.drain())
        e.release("F19")
        e.advance(1000)
        self.assertEqual([], e.drain())

    def test_tap_then_quick_second_press_is_not_a_tap_hold(self):
        e = eng()
        e.tap("F19")
        e.advance(100)
        e.tap("F19")
        e.advance(WINDOW)
        self.assertEqual(["send:^{b}"], e.drain())

    def test_tap_hold_only_binding_single_tap_waits_window(self):
        e = eng()
        e.tap("F21")
        self.assertEqual([], e.drain())
        e.advance(WINDOW)
        self.assertEqual(["send:^{i}"], e.drain())

    def test_tap_hold_only_binding_tap_hold(self):
        e = eng()
        e.tap("F21")
        e.advance(50)
        e.press("F21")
        e.advance(HOLD)
        self.assertEqual(["send:^{j}"], e.drain())

    def test_tap_hold_only_second_quick_tap_is_just_two_taps(self):
        e = eng()
        e.tap("F21")
        e.tap("F21")
        e.advance(WINDOW)
        self.assertEqual(["send:^{i}"], e.drain(), "no doubleTap, so a tap once")

    def test_stale_wait_timer_after_reset_is_ignored(self):
        e = eng()
        e.tap("F18")
        e.machine.reset()
        e.advance(WINDOW)
        self.assertEqual([], e.drain())

    def test_interleaved_keys_do_not_share_counts(self):
        e = eng()
        e.tap("F18")
        e.tap("F19")
        e.tap("F18")
        self.assertEqual(["send:^+{s}"], e.drain())
        e.advance(WINDOW)
        self.assertEqual(["send:^{a}"], e.drain())


class TestTapWindowSetting(unittest.TestCase):
    def test_default_is_250(self):
        cfg = make_cfg()
        del cfg["tapWindowMs"]
        self.assertEqual(250, mirror.load_config_obj(cfg)["tapWindowMs"])

    def test_custom_window_is_used(self):
        e = mirror.FakeEngine(mirror.load_config_obj(make_cfg(tapWindowMs=400)))
        e.tap("F18")
        e.advance(399)
        self.assertEqual([], e.drain())
        e.advance(1)
        self.assertEqual(["send:^{s}"], e.drain())

    def test_out_of_range_or_wrong_type_rejected(self):
        for v in (49, 1001, 250.5, "250", True):
            errs = mirror.validate_config(make_cfg(tapWindowMs=v))
            self.assertTrue(any("tapWindowMs" in x for x in errs), v)


class TestReservedKeys(unittest.TestCase):
    def test_f16_and_f17_refused_as_bindings_any_case(self):
        for k in ("F16", "F17", "f16", "f17"):
            b = {k: {"tap": {"send": "Ctrl+Z"}}}
            errs = mirror.validate_config(make_cfg(bindings=b))
            self.assertTrue(any("reserved" in x and "Cidoo" in x for x in errs), k)

    def test_f16_and_f17_refused_as_layer_keys(self):
        cfg = make_cfg()
        cfg["layers"]["num"]["keys"]["f16"] = "1"
        errs = mirror.validate_config(cfg)
        self.assertTrue(any("layers.num.keys" in x and "reserved" in x for x in errs))

    def test_neighbours_are_still_bindable(self):
        for k in ("F15", "F18"):
            self.assertEqual([], mirror.validate_config(make_cfg(bindings={k: {"tap": {"send": "Ctrl+Z"}}})))

    def test_multitap_slots_validated_like_tap(self):
        b = {"F13": {"tap": {"send": "Ctrl+Z"}, "doubleTap": {"send": "Ctrl+Alt+Shift+V"}}}
        errs = mirror.validate_config(make_cfg(bindings=b))
        self.assertTrue(any(x.startswith("bindings.F13.doubleTap.send") for x in errs))
        b = {"F13": {"tap": {"send": "Ctrl+Z"}, "tapHold": {"send": "Hyper+Q"}}}
        errs = mirror.validate_config(make_cfg(bindings=b))
        self.assertTrue(any(x.startswith("bindings.F13.tapHold.send") for x in errs))

    def test_space_and_capslock_remain_protected(self):
        for k in ("space", "capslock"):
            errs = mirror.validate_config(make_cfg(bindings={k: {"tap": {"send": "Ctrl+Z"}}}))
            self.assertTrue(any("protected" in x for x in errs), k)


class TestShippedConfigsStayCompatible(unittest.TestCase):
    def test_shipped_and_example_still_valid_and_have_no_multi_in_shipped(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        self.assertEqual(250, c["tapWindowMs"])
        self.assertTrue(all(not b["multi"] for b in c["bindings"].values()))
        mirror.load_config_file(mirror.LAYERS_EXAMPLE_JSON)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class EncoderRig:
    """engine.ahk: EncDecide / EncPressDecide, with the AULA match decided by
    the test (aula=True/False) instead of raw input."""

    def __init__(self, encoder):
        self.cfg = mirror.load_config_obj(make_cfg(encoder=encoder))
        self.enc = self.cfg["encoder"]
        self.emitted = []
        self.fake = None
        if self.enc["press"]:
            self.fake = mirror.FakeEngine(
                {
                    "tapHoldMs": self.cfg["tapHoldMs"],
                    "tapWindowMs": self.cfg["tapWindowMs"],
                    "bindings": {"press": self.enc["press"]["entry"]},
                    "exeIndex": {},
                }
            )
        self.press_aula = False

    def drain(self):
        out = (self.fake.drain() if self.fake else []) + self.emitted
        self.emitted = []
        return out

    def advance(self, ms):
        self.fake.advance(ms)

    def turn(self, direction, aula=True):
        d = self.enc["dirs"].get(direction)
        if d is None:
            pass
            return
        if not aula:
            self.emitted.append("pass:" + d["src"])
            return
        if d["pressAction"] and self.fake and self.fake.machine.is_down("press"):
            self.fake._exec(self.fake.machine.consume("press"))
            self.fake._action(d["pressAction"])
            return
        if d["action"]:
            self.fake = self.fake or mirror.FakeEngine(self.cfg)
            self.fake._action(d["action"])
        else:
            self.emitted.append("pass:" + d["src"])

    def down(self, aula=True):
        if self.press_aula:
            self.fake._exec(self.fake.machine.on_down("press", self.fake.now))
            return
        if not aula:
            self.emitted.append("pass:{Volume_Mute}")
            return
        self.press_aula = True
        self.fake._exec(self.fake.machine.on_down("press", self.fake.now))

    def up(self):
        if self.press_aula:
            self.press_aula = False
            self.fake._exec(self.fake.machine.on_up("press", self.fake.now))

    def click(self, aula=True, ms=10):
        self.down(aula)
        self.fake.advance(ms)
        self.up()


FULL = {
    "left": {"send": "Volume_Down"},
    "right": {"send": "Volume_Up"},
    "press": {"send": "Media_Play_Pause"},
    "doublePress": {"send": "Media_Next"},
    "triplePress": {"send": "Media_Prev"},
    "hold": {"send": "Volume_Mute"},
    "pressLeft": {"send": "Ctrl+Z"},
    "pressRight": {"send": "Ctrl+Y"},
}


class TestEncoderCompile(unittest.TestCase):
    def test_stock_encoder_unchanged_and_not_in_press_mode(self):
        c = mirror.load_config_obj(make_cfg(encoder={"press": {"send": "Volume_Mute"}}))
        self.assertEqual({}, c["encoder"]["dirs"])
        self.assertEqual("", c["encoder"]["press"])

    def test_single_press_remap_keeps_legacy_single_shot_path(self):
        c = mirror.load_config_obj(make_cfg(encoder={"press": {"send": "Media_Play_Pause"}}))
        self.assertEqual({"press"}, set(c["encoder"]["dirs"]))
        self.assertEqual("", c["encoder"]["press"])

    def test_any_press_trigger_switches_press_to_a_machine(self):
        for t in ("doublePress", "triplePress", "hold", "pressLeft", "pressRight"):
            c = mirror.load_config_obj(make_cfg(encoder={t: {"send": "Ctrl+Z"}}))
            self.assertTrue(c["encoder"]["press"], t)
            self.assertNotIn("press", c["encoder"]["dirs"], t)

    def test_press_left_hooks_left_even_when_left_is_stock(self):
        c = mirror.load_config_obj(make_cfg(encoder={"pressLeft": {"send": "Ctrl+Z"}}))
        self.assertEqual({"left"}, set(c["encoder"]["dirs"]))
        self.assertEqual("", c["encoder"]["dirs"]["left"]["action"])

    def test_unknown_encoder_trigger_and_bad_action_rejected(self):
        errs = mirror.validate_config(make_cfg(encoder={"longPress": {"send": "Ctrl+Z"}}))
        self.assertTrue(any("unknown key 'longPress'" in e for e in errs))
        errs = mirror.validate_config(make_cfg(encoder={"hold": {"send": "Nope+Z"}}))
        self.assertTrue(any(e.startswith("encoder.hold.send") for e in errs))
        errs = mirror.validate_config(make_cfg(encoder={"pressLeft": {"send": "Ctrl+Alt+Shift+V"}}))
        self.assertTrue(any(e.startswith("encoder.pressLeft.send") for e in errs))


class TestEncoderBehaviour(unittest.TestCase):
    def test_single_press_waits_window_then_taps(self):
        r = EncoderRig(FULL)
        r.click()
        self.assertEqual([], r.drain())
        r.advance(WINDOW)
        self.assertEqual(["send:{Media_Play_Pause}"], r.drain())

    def test_double_and_triple_press(self):
        r = EncoderRig(FULL)
        r.click()
        r.click()
        r.advance(WINDOW)
        self.assertEqual(["send:{Media_Next}"], r.drain())
        for _ in range(3):
            r.click()
        self.assertEqual(["send:{Media_Prev}"], r.drain())

    def test_hold_fires_at_threshold_once(self):
        r = EncoderRig(FULL)
        r.down()
        r.advance(HOLD)
        self.assertEqual(["send:{Volume_Mute}"], r.drain())
        r.up()
        r.advance(1000)
        self.assertEqual([], r.drain())

    def test_press_without_press_slot_taps_stock_mute(self):
        r = EncoderRig({"doublePress": {"send": "Media_Next"}})
        r.click()
        r.advance(WINDOW)
        self.assertEqual(["send:{Volume_Mute}"], r.drain())

    def test_turn_while_held_runs_press_action_and_cancels_the_press(self):
        r = EncoderRig(FULL)
        r.down()
        r.advance(50)
        r.turn("left")
        r.turn("left")
        r.turn("right")
        r.advance(HOLD * 3)
        r.up()
        r.advance(1000)
        self.assertEqual(["send:^{z}", "send:^{z}", "send:^{y}"], r.drain())

    def test_turn_after_hold_fired_still_runs_press_action(self):
        r = EncoderRig(FULL)
        r.down()
        r.advance(HOLD)
        r.drain()
        r.turn("right")
        r.up()
        r.advance(1000)
        self.assertEqual(["send:^{y}"], r.drain())

    def test_plain_turn_when_not_pressed_uses_normal_action(self):
        r = EncoderRig({"pressLeft": {"send": "Ctrl+Z"}, "right": {"send": "Media_Next"}})
        r.fake = mirror.FakeEngine(r.cfg) if not r.fake else r.fake
        r.turn("left")
        self.assertEqual(["pass:{Volume_Down}"], r.drain())
        r.turn("right")
        self.assertEqual(["send:{Media_Next}"], r.drain())

    def test_turn_while_held_without_matching_slot_does_not_cancel_press(self):
        r = EncoderRig({"pressLeft": {"send": "Ctrl+Z"}, "hold": {"send": "Volume_Mute"}})
        r.down()
        r.turn("right")           # right is stock, no pressRight
        r.advance(HOLD)
        self.assertEqual(["send:{Volume_Mute}"], r.drain())

    def test_other_keyboards_pass_through_untouched(self):
        r = EncoderRig(FULL)
        r.click(aula=False)
        r.turn("left", aula=False)
        r.advance(WINDOW)
        self.assertEqual(["pass:{Volume_Mute}", "pass:{Volume_Down}"], r.drain())
        self.assertEqual("idle", r.fake.machine.phase("press"))


# ---------------------------------------------------------------------------
# Config path + watch
# ---------------------------------------------------------------------------


class TestConfigPath(unittest.TestCase):
    def test_app_written_file_wins_when_it_exists(self):
        p = mirror.resolve_config_path("C:\\Users\\x\\AppData\\Roaming", "C:\\svc", lambda f: True)
        self.assertEqual("C:\\Users\\x\\AppData\\Roaming\\aula-desktop\\hostlayer.json", p)

    def test_falls_back_to_layers_json(self):
        p = mirror.resolve_config_path("C:\\Users\\x\\AppData\\Roaming", "C:\\svc", lambda f: False)
        self.assertEqual("C:\\svc\\layers.json", p)

    def test_no_appdata_falls_back(self):
        self.assertEqual("C:\\svc\\layers.json", mirror.resolve_config_path("", "C:\\svc", lambda f: True))


class TestConfigWatch(unittest.TestCase):
    def test_unchanged_never_reloads(self):
        w = mirror.ConfigWatch("a")
        self.assertFalse(any(w.step("a") for _ in range(5)))

    def test_change_reloads_once_after_it_is_stable_for_two_polls(self):
        w = mirror.ConfigWatch("a")
        self.assertFalse(w.step("b"))
        self.assertTrue(w.step("b"))
        self.assertFalse(w.step("b"))

    def test_file_still_being_written_waits(self):
        w = mirror.ConfigWatch("a")
        self.assertFalse(w.step("b"))
        self.assertFalse(w.step("c"))
        self.assertTrue(w.step("c"))

    def test_manual_reload_marks_current_so_it_is_not_reloaded_again(self):
        w = mirror.ConfigWatch("a")
        w.step("b")
        w.mark("b")
        self.assertFalse(w.step("b"))

    def test_change_back_to_original_between_polls_is_ignored(self):
        w = mirror.ConfigWatch("a")
        w.step("b")
        self.assertFalse(w.step("a"))
        self.assertFalse(w.step("a"))


if __name__ == "__main__":
    unittest.main()

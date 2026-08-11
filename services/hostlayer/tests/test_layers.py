"""Gate tests: the F19 sticky num layer.

Revision 2 removed the momentary nav layer and every CapsLock/Space behaviour,
so the only layer left is the sticky num grid. The load-bearing property is
that turning it on changes twelve keys and nothing else -- Space in particular
must still be Space.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror  # noqa: E402

THRESHOLD = 200

# The shipped grid. Kept here as literal truth so a silent edit to layers.json
# fails the suite instead of quietly changing what the keyboard types.
NUM_GRID = {
    "u": "{7}", "i": "{8}", "o": "{9}",
    "j": "{4}", "k": "{5}", "l": "{6}",
    "m": "{1}", "comma": "{2}", "period": "{3}",
    "n": "{0}",
    "slash": "{.}",
    "semicolon": "{Enter}",
}


def shipped():
    return mirror.load_config_file(mirror.LAYERS_JSON)


def engine():
    return mirror.FakeEngine(shipped())


class TestShippedNumGrid(unittest.TestCase):
    def test_grid_matches_the_documented_layout(self):
        c = shipped()
        self.assertEqual(NUM_GRID, c["layers"]["num"]["keys"])

    def test_grid_has_twelve_keys(self):
        self.assertEqual(12, len(shipped()["layers"]["num"]["keys"]))

    def test_num_is_the_only_layer(self):
        self.assertEqual(["num"], shipped()["toggleLayers"])

    def test_num_is_sticky_not_momentary(self):
        self.assertEqual("toggle", shipped()["layers"]["num"]["type"])

    def test_num_layer_is_not_blind(self):
        """Digits must arrive clean, so a stray held Shift cannot turn 7 into &."""
        self.assertEqual(0, shipped()["layers"]["num"]["blind"])

    def test_digits_are_top_row_not_numpad(self):
        """Top-row digit codes work regardless of NumLock state."""
        for key in ("u", "i", "o", "j", "k", "l", "m", "comma", "period", "n"):
            self.assertNotIn("Numpad", shipped()["layers"]["num"]["keys"][key])


class TestStickyToggle(unittest.TestCase):
    def test_layer_starts_off(self):
        e = engine()
        self.assertEqual({}, e.active_toggles)

    def test_f19_turns_it_on(self):
        e = engine()
        e.tap("F19")
        self.assertIn("num", e.active_toggles)

    def test_f19_turns_it_off_again(self):
        e = engine()
        e.tap("F19")
        e.tap("F19")
        self.assertNotIn("num", e.active_toggles)

    def test_it_stays_on_across_other_keys(self):
        """Sticky, not momentary: releasing F19 must not drop the layer."""
        e = engine()
        e.tap("F19")
        e.drain()
        for _ in range(5):
            e.type_key("u")
        self.assertEqual(["send:{7}"] * 5, e.drain())
        self.assertIn("num", e.active_toggles)

    def test_toggle_survives_a_long_hold_of_f19(self):
        e = engine()
        e.press("F19")
        e.advance(2000)
        e.release("F19")
        self.assertEqual(["layer:num=on"], e.drain())


class TestKeysWhileLayerOn(unittest.TestCase):
    def test_every_grid_key_emits_its_digit(self):
        e = engine()
        e.tap("F19")
        e.drain()
        for key, expected in NUM_GRID.items():
            e.type_key(key)
            with self.subTest(key=key):
                self.assertEqual(["send:" + expected], e.drain())

    def test_home_row_is_456(self):
        e = engine()
        e.tap("F19")
        e.drain()
        for key in ("j", "k", "l"):
            e.type_key(key)
        self.assertEqual(["send:{4}", "send:{5}", "send:{6}"], e.drain())

    def test_top_row_is_789(self):
        e = engine()
        e.tap("F19")
        e.drain()
        for key in ("u", "i", "o"):
            e.type_key(key)
        self.assertEqual(["send:{7}", "send:{8}", "send:{9}"], e.drain())

    def test_bottom_row_is_123(self):
        e = engine()
        e.tap("F19")
        e.drain()
        for key in ("m", "comma", "period"):
            e.type_key(key)
        self.assertEqual(["send:{1}", "send:{2}", "send:{3}"], e.drain())

    def test_zero_sits_on_n(self):
        e = engine()
        e.tap("F19")
        e.drain()
        e.type_key("n")
        self.assertEqual(["send:{0}"], e.drain())

    def test_decimal_point_sits_on_slash(self):
        e = engine()
        e.tap("F19")
        e.drain()
        e.type_key("slash")
        self.assertEqual(["send:{.}"], e.drain())

    def test_enter_sits_on_semicolon(self):
        e = engine()
        e.tap("F19")
        e.drain()
        e.type_key("semicolon")
        self.assertEqual(["send:{Enter}"], e.drain())

    def test_typing_a_number_works_end_to_end(self):
        """1234.5 then Enter, the whole point of the layer."""
        e = engine()
        e.tap("F19")
        e.drain()
        for key in ("m", "comma", "period", "j", "slash", "k", "semicolon"):
            e.type_key(key)
        self.assertEqual(
            ["send:{1}", "send:{2}", "send:{3}", "send:{4}",
             "send:{.}", "send:{5}", "send:{Enter}"],
            e.drain(),
        )


class TestSpaceIsNeverTouched(unittest.TestCase):
    """Revision 2's hard requirement. Three independent proofs: the config
    cannot name it, the engine never registers it, and the resolver refuses it
    even if it somehow arrived."""

    def test_space_is_not_a_layer_key(self):
        self.assertNotIn("space", shipped()["layerKeys"])

    def test_space_is_not_intercepted_while_the_layer_is_on(self):
        e = engine()
        e.tap("F19")
        e.drain()
        self.assertFalse(e.layer_ctx("space"))

    def test_space_passes_straight_through_while_the_layer_is_on(self):
        e = engine()
        e.tap("F19")
        e.drain()
        e.type_key("space")
        self.assertEqual(["passthru:space"], e.drain())

    def test_resolver_refuses_space_even_if_asked(self):
        c = shipped()
        r = mirror.resolve_layer_key(c, "space", {"num": 1})
        self.assertEqual("passthrough", r["kind"])
        self.assertEqual("{Space}", r["send"])

    def test_capslock_is_equally_untouched(self):
        c = shipped()
        self.assertNotIn("capslock", c["layerKeys"])
        r = mirror.resolve_layer_key(c, "capslock", {"num": 1})
        self.assertEqual("passthrough", r["kind"])


class TestKeysWhileLayerOff(unittest.TestCase):
    """With the layer off the hotkey criterion is false, so these keys are
    never hooked at all and typing is byte-identical to no software running."""

    def test_grid_keys_pass_through_when_off(self):
        e = engine()
        for key in NUM_GRID:
            e.type_key(key)
        self.assertEqual(["passthru:" + k for k in NUM_GRID], e.drain())

    def test_layer_ctx_is_false_for_every_key_when_off(self):
        e = engine()
        for key in list(NUM_GRID) + ["space", "a", "z", "f13"]:
            with self.subTest(key=key):
                self.assertFalse(e.layer_ctx(key))

    def test_unmapped_keys_pass_through_even_when_on(self):
        e = engine()
        e.tap("F19")
        e.drain()
        for key in ("a", "z", "q", "5"):
            e.type_key(key)
        self.assertEqual(
            ["passthru:a", "passthru:z", "passthru:q", "passthru:5"], e.drain()
        )

    def test_layer_ctx_is_true_only_for_grid_keys_when_on(self):
        e = engine()
        e.tap("F19")
        e.drain()
        for key in NUM_GRID:
            with self.subTest(key=key, on=True):
                self.assertTrue(e.layer_ctx(key))
        for key in ("a", "z", "space", "q"):
            with self.subTest(key=key, on=False):
                self.assertFalse(e.layer_ctx(key))


class TestLayerPrecedence(unittest.TestCase):
    def test_toggle_layers_resolve_alphabetically(self):
        """Two active layers claiming one key must resolve the same way every
        run, whatever order the JSON listed them in."""
        cfg = {
            "schemaVersion": 1,
            "tapHoldMs": 200,
            "bindings": {"F19": {"tap": {"layerToggle": "alpha"}}},
            "layers": {
                "zeta": {"type": "toggle", "keys": {"u": "9"}},
                "alpha": {"type": "toggle", "keys": {"u": "1"}},
            },
        }
        c = mirror.load_config_obj(cfg)
        self.assertEqual(["alpha", "zeta"], c["toggleLayers"])
        r = mirror.resolve_layer_key(c, "u", {"alpha": 1, "zeta": 1})
        self.assertEqual("alpha", r["layer"])
        self.assertEqual("{1}", r["send"])

    def test_only_the_active_layer_claims_a_key(self):
        cfg = {
            "schemaVersion": 1,
            "tapHoldMs": 200,
            "bindings": {"F19": {"tap": {"layerToggle": "alpha"}}},
            "layers": {
                "zeta": {"type": "toggle", "keys": {"u": "9"}},
                "alpha": {"type": "toggle", "keys": {"u": "1"}},
            },
        }
        c = mirror.load_config_obj(cfg)
        r = mirror.resolve_layer_key(c, "u", {"zeta": 1})
        self.assertEqual("zeta", r["layer"])
        self.assertEqual("{9}", r["send"])

    def test_key_lookup_is_case_insensitive(self):
        c = shipped()
        self.assertEqual(
            mirror.resolve_layer_key(c, "U", {"num": 1})["send"],
            mirror.resolve_layer_key(c, "u", {"num": 1})["send"],
        )


class TestNoFRowLeakage(unittest.TestCase):
    """Correctness rule 1. The prior build leaked seven raw F1x presses to
    apps; every F13-F22 code must be claimed by a hotkey that swallows it."""

    def test_every_f_row_code_is_bound(self):
        c = shipped()
        for n in range(13, 23):
            self.assertIn("f%d" % n, c["bindings"])

    def test_no_binding_re_emits_its_own_f_key(self):
        c = shipped()
        for key, b in c["bindings"].items():
            for phase in ("tap", "hold"):
                a = b[phase]
                if a != "" and a.get("kind") == "send":
                    with self.subTest(key=key, phase=phase):
                        self.assertNotIn(
                            "{F1", a["send"],
                            "%s.%s would re-emit an F-row code" % (key, phase),
                        )
                        self.assertNotIn("{F2", a["send"])


if __name__ == "__main__":
    unittest.main()

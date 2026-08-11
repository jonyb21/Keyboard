"""Gate tests: config parsing, schema validation, and the shipped configs.

Deterministic, stdlib only, no device, no network. Target runtime well under
a second.
"""

import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror  # noqa: E402


def base_cfg():
    """A minimal config that validates clean. Tests mutate copies of it."""
    return {
        "schemaVersion": 1,
        "tapHoldMs": 200,
        "appGroups": {"solidworks": ["SLDWORKS.exe"], "excel": ["EXCEL.EXE"]},
        "bindings": {
            "F13": {
                "tap": {"send": "Ctrl+Z"},
                "hold": {
                    "byApp": {
                        "solidworks": {"send": "Ctrl+Y"},
                        "default": {"send": "Ctrl+Shift+Z"},
                    }
                },
            },
            "F19": {"tap": {"layerToggle": "num"}},
        },
        "layers": {
            "num": {"type": "toggle", "blind": False, "keys": {"u": "7", "i": "8"}},
        },
    }


class TestShippedConfigs(unittest.TestCase):
    def test_layers_json_parses(self):
        cfg = mirror.parse_config_text(mirror.read_config_text(mirror.LAYERS_JSON))
        self.assertIsInstance(cfg, dict)

    def test_layers_json_validates_clean(self):
        cfg = mirror.parse_config_text(mirror.read_config_text(mirror.LAYERS_JSON))
        self.assertEqual([], mirror.validate_config(cfg))

    def test_example_json_validates_clean(self):
        cfg = mirror.parse_config_text(
            mirror.read_config_text(mirror.LAYERS_EXAMPLE_JSON)
        )
        self.assertEqual([], mirror.validate_config(cfg))

    def test_layers_json_compiles(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        self.assertEqual(200, c["tapHoldMs"])

    def test_shipped_binds_exactly_f13_to_f22(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        self.assertEqual(
            sorted("f%d" % n for n in range(13, 23)), sorted(c["bindings"].keys())
        )

    def test_shipped_touches_no_key_outside_the_f_row_and_num_layer(self):
        """Revision 2: nothing but F13-F22 and the num layer keys is bound."""
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        bound = set(c["bindings"]) | set(c["layerKeys"])
        expected = {"f%d" % n for n in range(13, 23)} | {
            "u", "i", "o", "j", "k", "l", "m", "comma", "period", "n",
            "slash", "semicolon",
        }
        self.assertEqual(expected, bound)

    def test_shipped_never_binds_space_or_capslock(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        for protected in ("space", "capslock"):
            self.assertNotIn(protected, c["bindings"])
            self.assertNotIn(protected, c["layerKeys"])

    def test_shipped_f19_is_the_only_tap_only_binding(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        tap_only = [k for k, b in c["bindings"].items() if not b["hasHold"]]
        self.assertEqual(["f19"], tap_only)

    def test_shipped_taphold_pairs_are_present(self):
        """Revision 1 + 2 pairing: every non-F19 key has both a tap and a hold."""
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        for key in ("f13", "f14", "f15", "f16", "f17", "f18", "f20", "f21", "f22"):
            self.assertTrue(c["bindings"][key]["hasHold"], key + " must have a hold")

    def test_shipped_focus_app_targets(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        expect = {
            "f14": ("chrome.exe", "OUTLOOK.EXE"),
            "f16": ("SLDWORKS.exe", "explorer.exe"),
            "f17": ("InDesign (Beta).exe", "Photoshop.exe"),
        }
        for key, (tap_exe, hold_exe) in expect.items():
            self.assertEqual("focusApp", c["bindings"][key]["tap"]["kind"])
            self.assertEqual(tap_exe, c["bindings"][key]["tap"]["exe"])
            self.assertEqual("focusApp", c["bindings"][key]["hold"]["kind"])
            self.assertEqual(hold_exe, c["bindings"][key]["hold"]["exe"])

    def test_shipped_focus_app_launch_paths_exist_on_disk(self):
        """Every launch path recorded in layers.json must be a real file."""
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        checked = 0
        for key, b in c["bindings"].items():
            for phase in ("tap", "hold"):
                a = b[phase]
                if a != "" and a.get("kind") == "focusApp" and a.get("launch"):
                    self.assertTrue(
                        os.path.isfile(a["launch"]),
                        "%s.%s launch path missing: %s" % (key, phase, a["launch"]),
                    )
                    checked += 1
        self.assertEqual(6, checked)

    def test_shipped_explorer_binding_uses_a_window_class(self):
        """explorer.exe is also the desktop and taskbar; without a class filter
        the engine would always match and never open a new window."""
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        self.assertEqual("CabinetWClass", c["bindings"]["f16"]["hold"]["windowClass"])

    def test_shipped_f20_is_paste_then_copy(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        self.assertEqual("^{v}", c["bindings"]["f20"]["tap"]["send"])
        self.assertEqual("^{c}", c["bindings"]["f20"]["hold"]["send"])

    def test_shipped_f13_redo_is_app_aware(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        hold = c["bindings"]["f13"]["hold"]
        idx = c["exeIndex"]
        self.assertEqual("^{y}", mirror.resolve_app(hold, "SLDWORKS.exe", idx)["send"])
        self.assertEqual("^{y}", mirror.resolve_app(hold, "EXCEL.EXE", idx)["send"])
        self.assertEqual("^+{z}", mirror.resolve_app(hold, "notepad.exe", idx)["send"])
        self.assertEqual("^+{z}", mirror.resolve_app(hold, "", idx)["send"])

    def test_app_match_is_case_insensitive(self):
        c = mirror.load_config_file(mirror.LAYERS_JSON)
        hold = c["bindings"]["f13"]["hold"]
        self.assertEqual(
            "^{y}", mirror.resolve_app(hold, "sldworks.exe", c["exeIndex"])["send"]
        )


class TestBomTolerance(unittest.TestCase):
    def test_bom_prefixed_config_still_loads(self):
        cfg_text = '{"schemaVersion": 1}'
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bom.json")
            with open(p, "wb") as fh:
                fh.write(b"\xef\xbb\xbf" + cfg_text.encode("utf-8"))
            text = mirror.read_config_text(p)
            self.assertTrue(text.startswith("{"))
            self.assertEqual(1, mirror.parse_config_text(text)["schemaVersion"])

    def test_config_without_bom_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plain.json")
            with open(p, "wb") as fh:
                fh.write(b'{"schemaVersion": 1}')
            self.assertTrue(mirror.read_config_text(p).startswith("{"))


class TestSchemaRejections(unittest.TestCase):
    def assertRejects(self, cfg, needle):
        errs = mirror.validate_config(cfg)
        self.assertTrue(errs, "expected a validation error, config was accepted")
        joined = " | ".join(errs)
        self.assertIn(needle, joined)

    def test_unknown_root_key_rejected(self):
        cfg = base_cfg()
        cfg["turboMode"] = True
        self.assertRejects(cfg, "unknown key 'turboMode'")

    def test_unknown_binding_key_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["doubleTap"] = {"send": "Ctrl+X"}
        self.assertRejects(cfg, "unknown key 'doubleTap'")

    def test_unknown_action_key_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"]["repeat"] = True
        self.assertRejects(cfg, "unknown key 'repeat'")

    def test_wrong_schema_version_rejected(self):
        cfg = base_cfg()
        cfg["schemaVersion"] = 2
        self.assertRejects(cfg, "schemaVersion")

    def test_threshold_below_bound_rejected(self):
        cfg = base_cfg()
        cfg["tapHoldMs"] = 49
        self.assertRejects(cfg, "between 50 and 1000")

    def test_threshold_above_bound_rejected(self):
        cfg = base_cfg()
        cfg["tapHoldMs"] = 1001
        self.assertRejects(cfg, "between 50 and 1000")

    def test_threshold_bounds_are_inclusive(self):
        for v in (50, 1000):
            cfg = base_cfg()
            cfg["tapHoldMs"] = v
            self.assertEqual([], mirror.validate_config(cfg))

    def test_non_integer_threshold_rejected(self):
        cfg = base_cfg()
        cfg["tapHoldMs"] = 200.5
        self.assertRejects(cfg, "must be an integer")

    def test_missing_threshold_rejected(self):
        cfg = base_cfg()
        del cfg["tapHoldMs"]
        self.assertRejects(cfg, "tapHoldMs: required")

    def test_binding_without_tap_rejected(self):
        cfg = base_cfg()
        del cfg["bindings"]["F13"]["tap"]
        self.assertRejects(cfg, "tap: required")

    def test_action_with_two_kinds_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"send": "Ctrl+Z", "swallow": True}
        self.assertRejects(cfg, "exactly one of")

    def test_action_with_no_kind_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"note": "nothing here"}
        self.assertRejects(cfg, "exactly one of")

    def test_unknown_key_name_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F99"] = {"tap": {"send": "Ctrl+Z"}}
        self.assertRejects(cfg, "not a bindable physical key name")

    def test_unknown_chord_key_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"send": "Ctrl+Frobnicate"}
        self.assertRejects(cfg, "unknown key")

    def test_unknown_modifier_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"send": "Hyper+Z"}
        self.assertRejects(cfg, "unknown modifier")

    def test_unknown_builtin_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"builtin": "teleport"}
        self.assertRejects(cfg, "unknown builtin")

    def test_layer_toggle_to_missing_layer_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F19"]["tap"] = {"layerToggle": "ghost"}
        self.assertRejects(cfg, "no layer named 'ghost'")

    def test_byapp_without_default_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["hold"] = {"byApp": {"solidworks": {"send": "Ctrl+Y"}}}
        self.assertRejects(cfg, "'default' branch is required")

    def test_byapp_with_undeclared_group_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["hold"] = {
            "byApp": {"blender": {"send": "Ctrl+Y"}, "default": {"send": "Ctrl+Y"}}
        }
        self.assertRejects(cfg, "not a declared appGroup")

    def test_nested_byapp_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["hold"] = {
            "byApp": {
                "default": {"byApp": {"default": {"send": "Ctrl+Y"}}},
            }
        }
        self.assertRejects(cfg, "cannot be nested")

    def test_duplicate_exe_across_groups_rejected(self):
        cfg = base_cfg()
        cfg["appGroups"]["cad"] = ["SLDWORKS.exe"]
        self.assertRejects(cfg, "already claimed by group")

    def test_non_exe_in_app_group_rejected(self):
        cfg = base_cfg()
        cfg["appGroups"]["solidworks"] = ["SLDWORKS"]
        self.assertRejects(cfg, "must be exe names ending in .exe")

    def test_momentary_layer_type_rejected(self):
        """Revision 2 removed momentary layers; only 'toggle' survives."""
        cfg = base_cfg()
        cfg["layers"]["num"]["type"] = "momentary"
        self.assertRejects(cfg, "must be 'toggle'")


class TestProtectedKeys(unittest.TestCase):
    """Revision 2: Space and CapsLock must stay stock. The guard is structural,
    not a comment."""

    def assertRejects(self, cfg, needle):
        errs = mirror.validate_config(cfg)
        self.assertTrue(errs, "expected a validation error, config was accepted")
        self.assertIn(needle, " | ".join(errs))

    def test_binding_space_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["space"] = {"tap": {"send": "Space"}}
        self.assertRejects(cfg, "'space' is protected")

    def test_binding_capslock_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["capslock"] = {"tap": {"send": "Esc"}}
        self.assertRejects(cfg, "'capslock' is protected")

    def test_layer_mapping_space_rejected(self):
        cfg = base_cfg()
        cfg["layers"]["num"]["keys"]["space"] = "0"
        self.assertRejects(cfg, "'space' is protected")

    def test_layer_mapping_capslock_rejected(self):
        cfg = base_cfg()
        cfg["layers"]["num"]["keys"]["capslock"] = "0"
        self.assertRejects(cfg, "'capslock' is protected")


class TestForbiddenChordGuard(unittest.TestCase):
    """Ctrl+Alt+Shift+V is owned by InDesign Paste in Place. Binding it once
    already cost a day of debugging."""

    def assertRejects(self, cfg):
        errs = mirror.validate_config(cfg)
        self.assertTrue(errs)
        self.assertIn("Ctrl+Alt+Shift+V is reserved", " | ".join(errs))

    def test_tap_binding_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"send": "Ctrl+Alt+Shift+V"}
        self.assertRejects(cfg)

    def test_hold_binding_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["hold"] = {"send": "Ctrl+Alt+Shift+V"}
        self.assertRejects(cfg)

    def test_byapp_branch_rejected(self):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["hold"] = {
            "byApp": {
                "solidworks": {"send": "Ctrl+Alt+Shift+V"},
                "default": {"send": "Ctrl+Y"},
            }
        }
        self.assertRejects(cfg)

    def test_layer_key_rejected(self):
        cfg = base_cfg()
        cfg["layers"]["num"]["keys"]["u"] = "Ctrl+Alt+Shift+V"
        self.assertRejects(cfg)

    def test_modifier_order_does_not_matter(self):
        for spelling in (
            "Ctrl+Alt+Shift+V",
            "Shift+Alt+Ctrl+V",
            "Alt+Shift+Ctrl+V",
            "control+alt+shift+v",
        ):
            cfg = base_cfg()
            cfg["bindings"]["F13"]["tap"] = {"send": spelling}
            with self.subTest(spelling=spelling):
                self.assertRejects(cfg)

    def test_similar_chords_are_still_allowed(self):
        """The guard must be exact, not a blanket ban on Ctrl+Alt+Shift."""
        for spelling in ("Ctrl+Alt+Shift+E", "Ctrl+Shift+V", "Ctrl+Alt+V",
                         "Alt+Shift+V", "Ctrl+Alt+Shift+Win+V"):
            cfg = base_cfg()
            cfg["bindings"]["F13"]["tap"] = {"send": spelling}
            with self.subTest(spelling=spelling):
                self.assertEqual([], mirror.validate_config(cfg))


class TestFocusAppSchema(unittest.TestCase):
    def assertRejects(self, focus, needle):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"focusApp": focus}
        errs = mirror.validate_config(cfg)
        self.assertTrue(errs, "expected a validation error, config was accepted")
        self.assertIn(needle, " | ".join(errs))

    def assertAccepts(self, focus):
        cfg = base_cfg()
        cfg["bindings"]["F13"]["tap"] = {"focusApp": focus}
        self.assertEqual([], mirror.validate_config(cfg))

    def test_exe_is_required(self):
        self.assertRejects({"launch": "C:\\x\\y.exe"}, "focusApp.exe: required")

    def test_exe_must_end_in_exe(self):
        self.assertRejects({"exe": "chrome"}, "must end in .exe")

    def test_empty_exe_rejected(self):
        self.assertRejects({"exe": "   "}, "focusApp.exe: required")

    def test_launch_is_optional(self):
        self.assertAccepts({"exe": "chrome.exe"})

    def test_empty_launch_means_focus_only(self):
        self.assertAccepts({"exe": "chrome.exe", "launch": ""})

    def test_relative_launch_rejected(self):
        self.assertRejects(
            {"exe": "chrome.exe", "launch": "chrome.exe"}, "must be an absolute path"
        )

    def test_launch_must_point_at_an_exe(self):
        self.assertRejects(
            {"exe": "chrome.exe", "launch": "C:\\Program Files\\Google"},
            "must point at an .exe",
        )

    def test_unc_launch_path_accepted(self):
        self.assertAccepts({"exe": "app.exe", "launch": "\\\\server\\share\\app.exe"})

    def test_window_class_accepted(self):
        self.assertAccepts(
            {"exe": "explorer.exe", "windowClass": "CabinetWClass",
             "launch": "C:\\Windows\\explorer.exe"}
        )

    def test_unknown_focus_app_key_rejected(self):
        self.assertRejects(
            {"exe": "chrome.exe", "profile": "default"}, "unknown key 'profile'"
        )

    def test_non_string_window_class_rejected(self):
        self.assertRejects({"exe": "x.exe", "windowClass": 7}, "must be a string")


class TestChordCompilation(unittest.TestCase):
    def test_single_key(self):
        self.assertEqual("{z}", mirror.parse_chord("Z")["send"])

    def test_modifier_prefixes(self):
        self.assertEqual("^{z}", mirror.parse_chord("Ctrl+Z")["send"])
        self.assertEqual("^+{z}", mirror.parse_chord("Ctrl+Shift+Z")["send"])
        self.assertEqual("+#{s}", mirror.parse_chord("Shift+Win+S")["send"])

    def test_modifier_order_is_normalised(self):
        """Spelling order in the config must not change the compiled output."""
        self.assertEqual(
            mirror.parse_chord("Ctrl+Shift+Z")["send"],
            mirror.parse_chord("Shift+Ctrl+Z")["send"],
        )

    def test_media_keys(self):
        self.assertEqual("{Volume_Mute}", mirror.parse_chord("Volume_Mute")["send"])
        self.assertEqual("{Media_Next}", mirror.parse_chord("Media_Next")["send"])

    def test_punctuation_keys(self):
        self.assertEqual("{.}", mirror.parse_chord("Period")["send"])
        self.assertEqual("{,}", mirror.parse_chord("Comma")["send"])
        self.assertEqual("{;}", mirror.parse_chord("Semicolon")["send"])

    def test_chord_names_are_case_insensitive(self):
        self.assertEqual("^{z}", mirror.parse_chord("ctrl+z")["send"])
        self.assertEqual("^{z}", mirror.parse_chord("CTRL+Z")["send"])

    def test_empty_chord_rejected(self):
        with self.assertRaises(mirror.ChordError):
            mirror.parse_chord("")


class TestConfigDeepCopyIsolation(unittest.TestCase):
    """Guards the tests themselves: base_cfg must hand back a fresh object."""

    def test_base_cfg_is_not_shared(self):
        a = base_cfg()
        b = base_cfg()
        a["bindings"]["F13"]["tap"]["send"] = "Ctrl+Q"
        self.assertEqual("Ctrl+Z", b["bindings"]["F13"]["tap"]["send"])

    def test_deepcopy_of_shipped_config_validates(self):
        cfg = mirror.parse_config_text(mirror.read_config_text(mirror.LAYERS_JSON))
        self.assertEqual([], mirror.validate_config(copy.deepcopy(cfg)))


if __name__ == "__main__":
    unittest.main()

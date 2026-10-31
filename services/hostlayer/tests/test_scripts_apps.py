"""run / ahk action kinds and per-app profiles ("apps").

Deterministic, stdlib only, no device, nothing is launched.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror  # noqa: E402
from test_config import base_cfg  # noqa: E402


def cfg_with(action):
    cfg = base_cfg()
    cfg["bindings"]["F19"] = {"tap": action}
    return cfg


def errs_of(cfg):
    return " | ".join(mirror.validate_config(cfg))


class TestRunAction(unittest.TestCase):
    def test_valid_run_accepted_and_compiled(self):
        cfg = cfg_with({"run": {"path": "C:\\x\\y.ps1", "args": "-a 1", "hide": True}})
        self.assertEqual([], mirror.validate_config(cfg))
        a = mirror.compile_config(cfg)["bindings"]["f19"]["tap"]
        self.assertEqual("run", a["kind"])
        self.assertEqual(("C:\\x\\y.ps1", "-a 1", 1), (a["path"], a["args"], a["hide"]))

    def test_relative_path_rejected(self):
        self.assertIn("absolute path", errs_of(cfg_with({"run": {"path": "y.ps1"}})))

    def test_missing_path_rejected(self):
        self.assertIn("run.path: required", errs_of(cfg_with({"run": {}})))

    def test_quote_in_path_rejected(self):
        self.assertIn("double quote", errs_of(cfg_with({"run": {"path": 'C:\\a"b.ps1'}})))

    def test_bad_args_hide_and_unknown_key_rejected(self):
        e = errs_of(cfg_with({"run": {"path": "C:\\a.py", "args": 3, "hide": "y", "x": 1}}))
        self.assertIn("run.args: must be a string", e)
        self.assertIn("run.hide: must be true or false", e)
        self.assertIn("unknown key 'x'", e)

    def test_run_plus_send_is_two_kinds(self):
        self.assertIn("exactly one of", errs_of(cfg_with({"run": {"path": "C:\\a.py"}, "swallow": True})))

    def test_commands_by_extension(self):
        def cmds(path, args=""):
            return mirror.run_commands({"path": path, "args": args}, "C:\\AHK\\a.exe", "C:\\cmd.exe")

        self.assertEqual(
            ['powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\\x y\\a.ps1" -n 1'],
            cmds("C:\\x y\\a.ps1", "-n 1"),
        )
        self.assertEqual(['py.exe "C:\\a.PY"', 'python.exe "C:\\a.PY"'], cmds("C:\\a.PY"))
        self.assertEqual(['"C:\\cmd.exe" /c ""C:\\a.bat" x"'], cmds("C:\\a.bat", "x"))
        self.assertEqual(['"C:\\cmd.exe" /c ""C:\\a.cmd""'], cmds("C:\\a.cmd"))
        self.assertEqual(['"C:\\AHK\\a.exe" "C:\\a.ahk"'], cmds("C:\\a.ahk"))
        self.assertEqual(['"C:\\doc.txt"'], cmds("C:\\doc.txt"))
        self.assertEqual(['"C:\\dir.v2\\noext"'], cmds("C:\\dir.v2\\noext"))

    def test_path_is_quoted_never_split(self):
        c = mirror.run_commands({"path": "C:\\a b & c\\s.ps1", "args": ""}, "", "")[0]
        self.assertTrue(c.endswith('-File "C:\\a b & c\\s.ps1"'))

    def test_engine_fires_run_action(self):
        eng = mirror.FakeEngine(mirror.compile_config(cfg_with({"run": {"path": "C:\\a.ps1"}})))
        eng.press("f19")
        self.assertEqual(["run:C:\\a.ps1"], eng.drain())


class TestAhkAction(unittest.TestCase):
    def test_valid_snippet_compiled(self):
        cfg = cfg_with({"ahk": "MsgBox 'hi'"})
        self.assertEqual([], mirror.validate_config(cfg))
        a = mirror.compile_config(cfg)["bindings"]["f19"]["tap"]
        self.assertEqual(("ahk", "MsgBox 'hi'"), (a["kind"], a["code"]))

    def test_empty_and_non_string_rejected(self):
        self.assertIn("ahk: must be a non-empty", errs_of(cfg_with({"ahk": "  "})))
        self.assertIn("ahk: must be a non-empty", errs_of(cfg_with({"ahk": 5})))

    def test_size_limit_8kb(self):
        self.assertEqual("", errs_of(cfg_with({"ahk": "a" * 8192})))
        self.assertIn("8 KB", errs_of(cfg_with({"ahk": "a" * 8193})))
        # the limit is bytes, not characters
        self.assertIn("8 KB", errs_of(cfg_with({"ahk": "\u00e9" * 4097})))

    def test_hash_stable_and_content_addressed(self):
        self.assertEqual(snippet := mirror.snippet_hash("MsgBox 1"), mirror.snippet_hash("MsgBox 1"))
        self.assertNotEqual(snippet, mirror.snippet_hash("MsgBox 2"))
        self.assertEqual(16, len(snippet))
        # FNV-1a 64 reference value for the empty string is the offset basis
        self.assertEqual("cbf29ce484222325", mirror.snippet_hash(""))
        self.assertEqual("af63dc4c8601ec8c", mirror.snippet_hash("a"))

    def test_source_has_requires_header(self):
        self.assertEqual("#Requires AutoHotkey v2.0\nX\n", mirror.snippet_source("X"))

    def test_engine_fires_ahk_action(self):
        eng = mirror.FakeEngine(mirror.compile_config(cfg_with({"ahk": "Beep"})))
        eng.press("f19")
        self.assertEqual(["ahk:" + mirror.snippet_hash("Beep")], eng.drain())


def apps_cfg():
    cfg = base_cfg()
    cfg["encoder"] = {
        "devices": ["VID_0C45&PID_800A"],
        "left": {"send": "Ctrl+Z"},
    }
    cfg["apps"] = {
        "solidworks": {
            "bindings": {
                "F19": {"tap": {"send": "Ctrl+S"}},
                "f13": {"tap": {"send": "Ctrl+Y"}},
            },
            "encoder": {"left": {"send": "Ctrl+Shift+Z"}, "right": {"send": "PgDn"}},
        },
        "excel": {"bindings": {"F13": {"hold": {"swallow": True}}}},
    }
    return cfg


def fire(cfg, exe, key, hold=False):
    eng = mirror.FakeEngine(mirror.compile_config(cfg), active_exe=exe)
    eng.press(key)
    if hold:
        eng.advance(500)
    eng.release(key)
    eng.advance(500)
    return eng.drain()


class TestAppsProfiles(unittest.TestCase):
    def test_valid_and_example_shape(self):
        self.assertEqual([], mirror.validate_config(apps_cfg()))

    def test_override_beats_global_in_app(self):
        self.assertEqual(["send:^{s}"], fire(apps_cfg(), "SLDWORKS.EXE", "f19"))

    def test_other_apps_keep_global(self):
        self.assertEqual(["layer:num=on"], fire(apps_cfg(), "chrome.exe", "f19"))
        self.assertEqual(["layer:num=on"], fire(apps_cfg(), "", "f19"))

    def test_key_name_case_insensitive_merge(self):
        # "f13" in the profile merges into the global "F13", not a new key
        c = mirror.compile_config(apps_cfg())
        self.assertEqual(2, len(c["bindings"]))
        self.assertEqual(["send:^{y}"], fire(apps_cfg(), "SLDWORKS.exe", "F13"))
        self.assertEqual(["send:^{z}"], fire(apps_cfg(), "notepad.exe", "F13"))

    def test_trigger_not_overridden_keeps_global_byapp(self):
        # global hold is already byApp: solidworks branch kept, excel added
        cfg = apps_cfg()
        self.assertEqual(["send:^{y}"], fire(cfg, "SLDWORKS.exe", "F13", hold=True))
        self.assertEqual(["swallow"], fire(cfg, "excel.exe", "F13", hold=True))
        self.assertEqual(["send:^+{z}"], fire(cfg, "chrome.exe", "F13", hold=True))

    def test_override_replaces_global_byapp_branch(self):
        cfg = apps_cfg()
        cfg["apps"]["solidworks"]["bindings"]["f13"]["hold"] = {"send": "Ctrl+Alt+Y"}
        self.assertEqual(["send:^!{y}"], fire(cfg, "SLDWORKS.exe", "F13", hold=True))

    def test_source_config_not_mutated(self):
        cfg = apps_cfg()
        before = copy.deepcopy(cfg)
        mirror.compile_config(cfg)
        self.assertEqual(before, cfg)

    def test_new_key_only_in_profile(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F24"] = {"tap": {"send": "Ctrl+B"}}
        self.assertEqual(["send:^{b}"], fire(cfg, "EXCEL.EXE", "F24"))
        self.assertEqual(["swallow"], fire(cfg, "chrome.exe", "F24"))

    def test_profile_added_trigger_falls_through_to_global_tap(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F19"] = {"doubleTap": {"send": "Ctrl+D"}}
        self.assertEqual(["layer:num=on"], fire(cfg, "chrome.exe", "F19"))
        self.assertNotIn("swallow", fire(cfg, "chrome.exe", "F19"))

    def test_profile_hold_long_press_outside_app_sends_global_tap(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F19"] = {"hold": {"send": "Ctrl+Q"}}
        self.assertEqual(["layer:num=on"], fire(cfg, "chrome.exe", "F19", hold=True))

    def test_profile_only_ordinary_key_rejected(self):
        for k in ("a", "Enter"):
            cfg = apps_cfg()
            cfg["apps"]["excel"]["bindings"][k] = {"tap": {"send": "Ctrl+B"}}
            self.assertTrue(any("not bound globally" in e for e in mirror.validate_config(cfg)), k)

    def test_encoder_override_and_stock_default(self):
        c = mirror.compile_config(apps_cfg())
        left, right = c["encoder"]["dirs"]["left"], c["encoder"]["dirs"]["right"]
        sw = mirror.resolve_app(left["action"], "SLDWORKS.exe", c["exeIndex"])
        self.assertEqual("^+{z}", sw["send"])
        self.assertEqual("^{z}", mirror.resolve_app(left["action"], "x.exe", c["exeIndex"])["send"])
        # right was stock globally: outside the app it must stay the stock key
        self.assertEqual("{Volume_Up}", mirror.resolve_app(right["action"], "x.exe", c["exeIndex"])["send"])
        self.assertEqual("{PgDn}", mirror.resolve_app(right["action"], "SLDWORKS.exe", c["exeIndex"])["send"])

    def test_encoder_press_mode_slot_from_profile(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["encoder"] = {"doublePress": {"send": "Ctrl+B"}}
        c = mirror.compile_config(cfg)
        self.assertEqual("byApp", c["encoder"]["press"]["entry"]["doubleTap"]["kind"])

    def test_run_and_ahk_allowed_in_profile(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F19"] = {"tap": {"run": {"path": "C:\\a.ps1"}}}
        self.assertEqual([], mirror.validate_config(cfg))
        self.assertEqual(["run:C:\\a.ps1"], fire(cfg, "excel.exe", "F19"))

    def test_undeclared_group_rejected(self):
        cfg = apps_cfg()
        cfg["apps"]["nope"] = {"bindings": {"F19": {"tap": {"swallow": True}}}}
        self.assertIn("'nope' is not a declared appGroup", errs_of(cfg))

    def test_protected_reserved_and_unknown_keys_rejected(self):
        for key, needle in (("space", "protected"), ("F16", "reserved"), ("F99", "not a bindable")):
            cfg = apps_cfg()
            cfg["apps"]["excel"]["bindings"][key] = {"tap": {"swallow": True}}
            self.assertIn(needle, errs_of(cfg))

    def test_f17_never_bindable_via_profile(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F17"] = {"tap": {"swallow": True}}
        self.assertIn("reserved", errs_of(cfg))

    def test_nested_byapp_in_profile_rejected(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F19"] = {"tap": {"byApp": {"default": {"swallow": True}}}}
        self.assertIn("cannot be nested", errs_of(cfg))

    def test_forbidden_chord_rejected_in_profile(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F19"] = {"tap": {"send": "Ctrl+Alt+Shift+V"}}
        self.assertIn("reserved by InDesign", errs_of(cfg))

    def test_empty_override_and_unknown_keys_rejected(self):
        cfg = apps_cfg()
        cfg["apps"]["excel"]["bindings"]["F19"] = {"note": "x"}
        cfg["apps"]["excel"]["extra"] = 1
        cfg["apps"]["excel"]["encoder"] = {"bogus": {"swallow": True}}
        e = errs_of(cfg)
        self.assertIn("must override at least one", e)
        self.assertIn("unknown key 'extra'", e)
        self.assertIn("unknown key 'bogus'", e)

    def test_encoder_profile_needs_global_encoder(self):
        cfg = apps_cfg()
        del cfg["encoder"]
        self.assertIn("needs a top-level encoder block", errs_of(cfg))

    def test_space_and_capslock_never_registered(self):
        c = mirror.compile_config(apps_cfg())
        self.assertNotIn("space", c["bindings"])
        self.assertNotIn("capslock", c["bindings"])

    def test_shipped_example_has_apps_and_validates(self):
        cfg = mirror.parse_config_text(mirror.read_config_text(mirror.LAYERS_EXAMPLE_JSON))
        self.assertIn("apps", cfg)
        self.assertEqual([], mirror.validate_config(cfg))
        mirror.compile_config(cfg)


if __name__ == "__main__":
    unittest.main()

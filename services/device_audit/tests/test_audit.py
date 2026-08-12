from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.device_audit.audit import (
    ACTION_REPAIR_PROFILE,
    ACTION_STOP_WRONG_CONFIGURATOR,
    ACTION_STOP_WRONG_HARDWARE,
    ACTION_USE_MATCHING_CONFIGURATOR,
    EXPECTED_BUS_NAME,
    EXPECTED_CONFIGURATOR_NAME,
    EXPECTED_CONFIGURATOR_VERSION,
    EXPECTED_MODEL,
    EXPECTED_PID,
    EXPECTED_REGISTRY_DISPLAY_NAME,
    EXPECTED_REVISION,
    EXPECTED_VID,
    collect_audit,
    inspect_config_xml,
    inspect_profile_db,
    normalise_device_snapshot,
    normalise_registry_entries,
    parse_hardware_identity,
    recommend_compatibility,
    select_model_registry_entry,
)


def exact_evidence() -> dict:
    return {
        "hardware": {
            "present": True,
            "vid": EXPECTED_VID,
            "pid": EXPECTED_PID,
            "revision": EXPECTED_REVISION,
            "bus_reported_name": EXPECTED_BUS_NAME,
        },
        "configurator": {
            "installed": True,
            "version": EXPECTED_CONFIGURATOR_VERSION,
            "config_file_version": EXPECTED_CONFIGURATOR_VERSION,
            "software_name": EXPECTED_CONFIGURATOR_NAME,
            "keyboard_name": EXPECTED_MODEL,
            "target_vid": EXPECTED_VID,
            "target_pid": EXPECTED_PID,
            "target_product_name": EXPECTED_BUS_NAME,
        },
        "profile": {
            "healthy": True,
            "response_present": True,
            "response_level": 1,
        },
    }


def write_exact_config(path: Path) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <device.info>
    <keyboard name="{EXPECTED_MODEL}">
      <mode value="0" desc="USB" vid="{EXPECTED_VID}" pid="{EXPECTED_PID}"
            product_name="{EXPECTED_BUS_NAME}"
            hid_interface="VID_{EXPECTED_VID}&amp;PID_{EXPECTED_PID}&amp;MI_00" />
      <mode value="2" desc="2.4G TYPE-A" vid="05AC" pid="024F"
            product_name="2.4G Dongle" />
    </keyboard>
  </device.info>
  <software name="{EXPECTED_CONFIGURATOR_NAME}" version="Beta {EXPECTED_CONFIGURATOR_VERSION}" />
</config>
""",
        encoding="utf-8",
    )


def write_profile_db(path: Path, response: str | None = "2", active_count: int = 1) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE t_profile_data (
            profile INTEGER PRIMARY KEY,
            name TEXT,
            status INTEGER,
            app TEXT,
            type INTEGER
        );
        CREATE TABLE t_config_data (
            config_id INTEGER PRIMARY KEY,
            profile INTEGER,
            key TEXT,
            value TEXT
        );
        CREATE TABLE t_key_macro_data (key_id INTEGER PRIMARY KEY);
        CREATE TABLE t_light_data (light_id INTEGER PRIMARY KEY);
        CREATE TABLE t_ledframe_data (frame_id INTEGER PRIMARY KEY);
        """
    )
    profiles = [
        (1, "My Exclusive Config 1", 1 if active_count >= 1 else 0, "", 0),
        (2, "My Exclusive Config 2", 1 if active_count >= 2 else 0, "", 0),
        (3, "My Exclusive Config 3", 0, "", 0),
    ]
    connection.executemany(
        "INSERT INTO t_profile_data(profile,name,status,app,type) VALUES(?,?,?,?,?)",
        profiles,
    )
    values = [(1, 1, "version", "1.0.1"), (2, 1, "lightmode", "1")]
    if response is not None:
        values.append((3, 1, "key_respondtime", response))
    connection.executemany(
        "INSERT INTO t_config_data(config_id,profile,key,value) VALUES(?,?,?,?)",
        values,
    )
    connection.commit()
    connection.close()


class FakeProbe:
    def __init__(self, device_payload: dict, registry_payload: dict):
        self.device_payload = device_payload
        self.registry_payload = registry_payload

    def device_snapshot(self) -> dict:
        return self.device_payload

    def installed_configurators(self) -> dict:
        return self.registry_payload


def exact_device_payload() -> dict:
    return {
        "root": {
            "status": "OK",
            "class": "USB",
            "friendly_name": "USB Composite Device",
            "instance_id": r"USB\VID_0C45&PID_800A\fixture",
        },
        "hardware_ids": [
            r"USB\VID_0C45&PID_800A&REV_0108",
            r"USB\VID_0C45&PID_800A",
        ],
        "properties": {
            "DEVPKEY_Device_BusReportedDeviceDesc": EXPECTED_BUS_NAME,
            "DEVPKEY_Device_DriverProvider": "Microsoft",
            "DEVPKEY_Device_DriverVersion": "10.0.26100.1",
            "DEVPKEY_Device_DriverInfPath": "usb.inf",
        },
        "nodes": [
            {
                "status": "OK",
                "class": "USB",
                "friendly_name": "USB Composite Device",
                "instance_id": r"USB\VID_0C45&PID_800A\fixture",
            }
        ],
    }


class HardwareParsingTests(unittest.TestCase):
    def test_revision_bearing_hardware_id_wins(self) -> None:
        identity = parse_hardware_identity(
            [
                r"USB\VID_0C45&PID_800A",
                r"USB\VID_0C45&PID_800A&REV_0108",
            ]
        )
        self.assertEqual(
            identity,
            {"vid": "0C45", "pid": "800A", "revision": "0108"},
        )

    def test_exact_snapshot_requires_bus_name_revision_and_ok_status(self) -> None:
        device = normalise_device_snapshot(exact_device_payload())
        self.assertTrue(device["exact_match"])
        self.assertEqual(device["pnp_node_count"], 1)
        self.assertEqual(device["windows_hid_driver"]["provider"], "Microsoft")

        wrong = exact_device_payload()
        wrong["properties"]["DEVPKEY_Device_BusReportedDeviceDesc"] = "AULA F75"
        self.assertFalse(normalise_device_snapshot(wrong)["exact_match"])


class ConfiguratorTests(unittest.TestCase):
    def test_registry_selection_never_substitutes_adjacent_f75(self) -> None:
        entries = normalise_registry_entries(
            {
                "entries": [
                    {
                        "display_name": "AULA F75 version 2.0",
                        "display_version": "2.0",
                        "install_location": r"C:\wrong",
                        "source": "HKLM32",
                    },
                    {
                        "display_name": EXPECTED_REGISTRY_DISPLAY_NAME,
                        "display_version": EXPECTED_CONFIGURATOR_VERSION,
                        "install_location": r"C:\right",
                        "source": "HKLM32",
                    },
                ]
            }
        )
        selected = select_model_registry_entry(entries)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["install_location"], r"C:\right")

    def test_config_xml_proves_version_model_and_usb_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.xml"
            write_exact_config(path)
            result = inspect_config_xml(path)
        self.assertTrue(result["exact_match"])
        self.assertEqual(result["software_version"], EXPECTED_CONFIGURATOR_VERSION)
        self.assertEqual(result["usb_target"]["pid"], EXPECTED_PID)
        self.assertRegex(result["sha256"], r"^[0-9A-F]{64}$")

    def test_config_xml_rejects_iso_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.xml"
            write_exact_config(path)
            text = path.read_text(encoding="utf-8").replace('pid="800A"', 'pid="80B1"')
            path.write_text(text, encoding="utf-8")
            result = inspect_config_xml(path)
        self.assertFalse(result["exact_match"])
        self.assertIsNone(result["usb_target"])

    def test_config_xml_accepts_vendor_bare_ampersands_without_changing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.xml"
            write_exact_config(path)
            source = path.read_text(encoding="utf-8").replace(
                "&amp;PID_800A&amp;MI_00", "&PID_800A&MI_00"
            )
            path.write_text(source, encoding="utf-8")
            before = path.read_bytes()
            result = inspect_config_xml(path)
            after = path.read_bytes()
        self.assertTrue(result["exact_match"])
        self.assertEqual(before, after)


class ProfileDatabaseTests(unittest.TestCase):
    def test_reads_active_profile_and_response_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.db"
            write_profile_db(path)
            before = path.read_bytes()
            result = inspect_profile_db(path)
            after = path.read_bytes()
        self.assertTrue(result["healthy"])
        self.assertEqual(result["active_profile"]["profile"], 1)
        self.assertEqual(result["response"]["raw_value"], "2")
        self.assertEqual(result["response"]["level_index"], 2)
        self.assertEqual(result["row_counts"]["t_profile_data"], 3)
        self.assertEqual(before, after)

    def test_missing_response_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.db"
            write_profile_db(path, response=None)
            result = inspect_profile_db(path)
        self.assertFalse(result["healthy"])
        self.assertFalse(result["response"]["present"])

    def test_two_active_profiles_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.db"
            write_profile_db(path, active_count=2)
            result = inspect_profile_db(path)
        self.assertFalse(result["healthy"])
        self.assertEqual(result["active_profile_count"], 2)

    def test_response_levels_one_and_five_are_valid(self) -> None:
        for level in ("1", "5"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.db"
                write_profile_db(path, response=level)
                result = inspect_profile_db(path)
            self.assertTrue(result["healthy"])
            self.assertTrue(result["response"]["valid_level"])

    def test_response_levels_outside_one_through_five_fail_closed(self) -> None:
        for level in ("0", "6", "not-a-number"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.db"
                write_profile_db(path, response=level)
                result = inspect_profile_db(path)
            self.assertFalse(result["healthy"])
            self.assertFalse(result["response"]["valid_level"])


class RecommendationTests(unittest.TestCase):
    def test_exact_contract_uses_only_matching_configurator(self) -> None:
        result = recommend_compatibility(exact_evidence())
        self.assertEqual(result["action"], ACTION_USE_MATCHING_CONFIGURATOR)
        self.assertTrue(result["safe_to_configure"])
        self.assertFalse(result["safe_to_flash_firmware"])

    def test_plain_f75_is_wrong_hardware(self) -> None:
        evidence = exact_evidence()
        evidence["hardware"].update(
            {"vid": "258A", "pid": "010C", "revision": None, "bus_reported_name": "AULA F75"}
        )
        result = recommend_compatibility(evidence)
        self.assertEqual(result["action"], ACTION_STOP_WRONG_HARDWARE)
        self.assertFalse(result["safe_to_configure"])

    def test_iso_driver_is_wrong_configurator_for_exact_hardware(self) -> None:
        evidence = exact_evidence()
        evidence["configurator"].update(
            {"version": "2.0.0.2", "target_pid": "80B1"}
        )
        result = recommend_compatibility(evidence)
        self.assertEqual(result["action"], ACTION_STOP_WRONG_CONFIGURATOR)
        self.assertFalse(result["safe_to_configure"])

    def test_unhealthy_profile_requires_repair(self) -> None:
        evidence = exact_evidence()
        evidence["profile"] = {
            "healthy": False,
            "response_present": False,
            "response_level": None,
        }
        result = recommend_compatibility(evidence)
        self.assertEqual(result["action"], ACTION_REPAIR_PROFILE)

    def test_zero_response_level_requires_repair_even_if_marked_healthy(self) -> None:
        evidence = exact_evidence()
        evidence["profile"]["response_level"] = 0
        result = recommend_compatibility(evidence)
        self.assertEqual(result["action"], ACTION_REPAIR_PROFILE)
        self.assertFalse(result["safe_to_configure"])


class CollectorTests(unittest.TestCase):
    def test_end_to_end_fixture_emits_ready_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.xml"
            database = root / "profile.db"
            write_exact_config(config)
            write_profile_db(database)
            registry = {
                "entries": [
                    {
                        "source": "HKLM32",
                        "display_name": EXPECTED_REGISTRY_DISPLAY_NAME,
                        "display_version": EXPECTED_CONFIGURATOR_VERSION,
                        "install_location": str(root),
                        "publisher": "",
                        "registry_key": "fixture",
                    }
                ]
            }
            snapshot = collect_audit(
                FakeProbe(exact_device_payload(), registry),
                config_xml_path=config,
                profile_db_path=database,
            )
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertIn('"schema_version": 1', encoded)
        self.assertTrue(snapshot["verdict"]["ready_for_efficiency_changes"])
        self.assertEqual(
            snapshot["recommendation"]["action"],
            ACTION_USE_MATCHING_CONFIGURATOR,
        )
        self.assertEqual(snapshot["profile_db"]["response"]["level_index"], 2)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import ctypes as ct
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import uproot
import yaml
from fastapi.testclient import TestClient

from telescopedaq.config import load_config, validate_config
from telescopedaq.event import Event
from telescopedaq.root_writer import RootWriter
from telescopedaq.triggers import configure_triggers
from telescopedaq.web.app import create_app
from telescopedaq.web.demo import DemoDigitizer
from telescopedaq.web.service import waveform_payload


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=".")
        self.workspace = Path(self.temp.name).resolve()
        data = copy.deepcopy(load_config("configs/channel0_generator_test.yaml").data)
        data["run"].update(output_dir="output", max_events=12, run_id=1)
        data["caen"]["record_length_samples"] = 64
        data["threshold"]["value_adc"] = 2200
        data["monitor"]["waveform_update_interval_s"] = 0.05
        self.config_path = self.workspace / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.app = create_app(self.config_path, self.workspace, demo=True)
        self.client = TestClient(self.app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def idle(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.client.get("/api/state").json()
            if not state["busy"]:
                return state
            time.sleep(0.01)
        self.fail("Worker did not finish")

    def test_safe_exit_requires_confirmation_and_is_idempotent(self):
        self.assertEqual(self.client.post("/api/shutdown", json={}).status_code, 422)
        self.client.post("/api/connect")
        self.idle()
        device = self.app.state.service.device
        payload = {"confirmation": "stop_and_exit"}
        self.assertEqual(
            self.client.post("/api/shutdown", json=payload).status_code, 202
        )
        future = self.app.state.service.shutdown_future
        self.assertEqual(
            self.client.post("/api/shutdown", json=payload).status_code, 202
        )
        self.assertIs(future, self.app.state.service.shutdown_future)
        future.result(timeout=5)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["shutdown_state"], "ready")
        self.assertFalse(device.is_open)
        self.assertEqual(self.client.post("/api/connect").status_code, 503)
        self.app.state.service.close()
        self.app.state.service.close()

    def test_lan_is_disabled_by_default(self):
        remote = TestClient(self.app, client=("192.168.50.20", 5000))
        self.assertEqual(remote.get("/api/state").status_code, 403)

    def test_lan_can_view_but_cannot_control_even_with_forwarded_headers(self):
        with patch(
            "telescopedaq.web.app.lan_addresses", return_value=["192.168.50.10"]
        ):
            app = create_app(self.config_path, self.workspace, demo=True, lan=True)
        with TestClient(
            app, client=("192.168.50.20", 5000), base_url="http://192.168.50.10"
        ) as remote:
            self.assertEqual(remote.get("/").status_code, 200)
            self.assertTrue(remote.get("/api/state").json()["read_only"])
            self.assertEqual(remote.get("/api/config").status_code, 200)
            for path in (
                "/api/connect",
                "/api/run/start",
                "/api/run/stop",
                "/api/run/emergency",
                "/api/scan/start",
                "/api/shutdown",
                "/api/root/upload",
            ):
                self.assertEqual(
                    remote.post(
                        path,
                        json={},
                        headers={
                            "X-Forwarded-For": "127.0.0.1",
                            "X-Real-IP": "127.0.0.1",
                        },
                    ).status_code,
                    403,
                    path,
                )
            self.assertEqual(remote.put("/api/config", json={}).status_code, 403)
            public = TestClient(app, client=("203.0.113.20", 5000))
            self.assertEqual(public.get("/").status_code, 403)
            local = TestClient(app, client=("127.0.0.1", 5000))
            self.assertFalse(local.get("/api/state").json()["read_only"])

    def test_safe_exit_waits_for_read_and_flushes_all_accepted_waveforms(self):
        started, release = threading.Event(), threading.Event()

        class SlowRead(DemoDigitizer):
            def read_events(self):
                batch = super().read_events()
                started.set()
                release.wait(5)
                return batch

        self.app.state.service.factory = SlowRead
        self.client.post("/api/run/start", json={"daq_mode": "write_only"})
        self.assertTrue(started.wait(3))
        try:
            self.client.post("/api/shutdown", json={"confirmation": "stop_and_exit"})
            self.assertEqual(
                self.client.get("/api/state").json()["shutdown_state"], "stopping"
            )
            self.assertFalse(self.app.state.service.shutdown_future.done())
        finally:
            release.set()
        self.app.state.service.shutdown_future.result(timeout=5)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["shutdown_state"], "ready")
        self.assertEqual(state["status"]["total_events"], 4)
        with uproot.open(self.workspace / "output/demo/run_000001.root") as root:
            self.assertEqual(root["events"].num_entries, 64)
        self.assertEqual(state["status"]["written_events"], 64)
        self.assertFalse(state["connected"])

    def test_safe_exit_reports_cleanup_error_without_false_success(self):
        class BadClose(DemoDigitizer):
            def close(self):
                super().close()
                raise RuntimeError("USB close failed")

        self.app.state.service.factory = BadClose
        self.client.post("/api/connect")
        self.idle()
        self.client.post("/api/shutdown", json={"confirmation": "stop_and_exit"})
        self.app.state.service.shutdown_future.result(timeout=5)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["shutdown_state"], "error")
        self.assertIn("USB close failed", state["shutdown_error"])
        self.assertEqual(self.client.get("/api/logs").status_code, 200)

    def test_safe_exit_during_failed_root_flush_stays_in_error(self):
        started, release = threading.Event(), threading.Event()

        class SlowRead(DemoDigitizer):
            def read_events(self):
                batch = super().read_events()
                started.set()
                release.wait(5)
                return batch

        self.app.state.service.factory = SlowRead
        with patch(
            "telescopedaq.root_writer.RootWriter.flush",
            side_effect=OSError("Disk full"),
        ):
            self.client.post("/api/run/start", json={})
            self.assertTrue(started.wait(3))
            try:
                self.client.post(
                    "/api/shutdown", json={"confirmation": "stop_and_exit"}
                )
            finally:
                release.set()
            self.app.state.service.shutdown_future.result(timeout=5)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["shutdown_state"], "error")
        self.assertIn("Disk full", state["shutdown_error"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_multichannel_run_and_offline_reader_no_derived_data(self):
        self.assertEqual(
            self.client.post(
                "/api/run/start",
                json={"daq_mode": "full_monitor", "trigger_mode": "threshold"},
            ).status_code,
            202,
        )
        state = self.idle()
        self.assertIsNone(state["error"])
        self.assertEqual(state["status"]["total_events"], 12)
        self.assertEqual(state["status"]["written_events"], 192)
        preview = self.client.get("/api/waveforms").json()
        self.assertEqual(len(preview["channels"]), 16)
        path = self.client.get("/api/files").json()[0]["path"]
        summary = self.client.get("/api/root/summary", params={"path": path}).json()
        self.assertEqual(summary["events"], 12)
        self.assertEqual(sum(summary["rate_y"]), 12)
        self.assertEqual(summary["rows"], 192)
        result = self.client.get(
            "/api/root/waveform", params={"path": path, "entry": 15}
        ).json()
        self.assertEqual(result["channel"], 15)
        with uproot.open(self.workspace / path) as root:
            self.assertFalse(
                {"amplitude", "baseline", "charge"}.intersection(root["events"].keys())
            )
        content = (self.workspace / path).read_bytes()
        self.assertEqual(self.client.post("/api/run/start", json={}).status_code, 409)
        self.assertEqual((self.workspace / path).read_bytes(), content)

    def test_disconnect_releases_handle_and_busy_external_run_stops(self):
        self.assertEqual(self.client.post("/api/connect").status_code, 202)
        self.assertEqual(self.idle()["state"], "connected")
        device = self.app.state.service.device
        self.assertTrue(device.is_open)
        self.client.post("/api/disconnect")
        self.idle()
        self.assertFalse(device.is_open)
        self.client.post(
            "/api/run/start",
            json={"daq_mode": "write_only", "trigger_mode": "external"},
        )
        self.assertEqual(self.client.post("/api/connect").status_code, 409)
        self.assertEqual(self.client.post("/api/scan/start", json={}).status_code, 409)
        self.assertEqual(
            self.client.put(
                "/api/config", json=self.client.get("/api/config").json()
            ).status_code,
            409,
        )
        self.client.post("/api/run/stop")
        self.assertIsNone(self.idle()["error"])
        self.assertEqual(self.client.get("/api/waveforms").json()["channels"], [])

    def test_validation_is_atomic_and_rejects_stale_or_invalid_settings(self):
        payload = self.client.get("/api/config").json()
        old = self.config_path.read_bytes()
        payload["data"]["caen"]["dc_offset"] = 70000
        self.assertEqual(self.client.put("/api/config", json=payload).status_code, 422)
        self.assertEqual(old, self.config_path.read_bytes())
        payload["data"]["caen"]["dc_offset"] = 32000
        self.assertEqual(self.client.put("/api/config", json=payload).status_code, 200)
        self.assertEqual(self.client.put("/api/config", json=payload).status_code, 409)
        payload = self.client.get("/api/config").json()
        payload["data"]["run"]["output_dir"] = "../outside"
        self.assertEqual(self.client.put("/api/config", json=payload).status_code, 422)

    def test_upload_and_origin_protection(self):
        result = self.client.post(
            "/api/root/upload", files={"file": ("bad.root", b"not root")}
        )
        self.assertEqual(result.status_code, 422)
        self.assertEqual(list((self.workspace / "output/imports").glob("*.root")), [])
        result = self.client.post(
            "/api/config/import", files={"file": ("bad.yaml", b"[1,2]")}
        )
        self.assertEqual(result.status_code, 422)
        self.assertEqual(
            self.client.post(
                "/api/connect", headers={"origin": "https://other.example"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/root/download", params={"path": "../secret.root"}
            ).status_code,
            422,
        )

    def test_reload_reads_external_yaml_changes(self):
        old = self.client.get("/api/config").json()
        data = copy.deepcopy(old["data"])
        data["run"]["run_id"] = 25
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.assertEqual(self.client.put("/api/config", json=old).status_code, 409)
        updated = self.client.get("/api/config").json()
        self.assertEqual(updated["data"]["run"]["run_id"], 25)
        self.assertEqual(self.client.put("/api/config", json=updated).status_code, 200)

    def test_connected_device_cannot_silently_switch_usb_address(self):
        self.client.post("/api/connect")
        self.idle()
        payload = self.client.get("/api/config").json()
        payload["data"]["caen"]["link_num"] = 1
        self.assertEqual(self.client.put("/api/config", json=payload).status_code, 409)
        self.client.post("/api/disconnect")
        self.idle()
        self.assertEqual(self.client.put("/api/config", json=payload).status_code, 200)

    def test_reload_cannot_relabel_an_already_open_usb_handle(self):
        self.client.post("/api/connect")
        self.idle()
        data = self.client.get("/api/config").json()["data"]
        data["caen"]["link_num"] = 1
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.assertEqual(self.client.get("/api/config").status_code, 409)
        self.client.post("/api/disconnect")
        self.idle()
        self.assertEqual(
            self.client.get("/api/config").json()["data"]["caen"]["link_num"], 1
        )

    def test_threshold_scan_still_counts_triggers_once(self):
        result = self.client.post(
            "/api/scan/start",
            json={
                "lower": 2200,
                "upper": 2220,
                "step": 10,
                "dwell_s": 0.1,
                "max_events": 4,
            },
        )
        self.assertEqual(result.status_code, 202)
        state = self.idle()
        self.assertEqual(state["scan"]["counts"], [4, 4, 4])
        self.assertEqual(self.client.get("/api/files").json(), [])

    def test_hardware_error_is_visible_and_server_recovers(self):
        class Failed(DemoDigitizer):
            def open(self, config):
                raise RuntimeError("USB unavailable")

        self.app.state.service.factory = Failed
        self.client.post("/api/connect")
        state = self.idle()
        self.assertEqual(state["state"], "error")
        self.assertIn("USB unavailable", state["error"])
        self.assertEqual(self.client.get("/").status_code, 200)
        self.app.state.service.factory = DemoDigitizer
        self.client.post("/api/connect")
        self.assertEqual(self.idle()["state"], "connected")

    def test_writer_failure_does_not_report_success(self):
        with patch(
            "telescopedaq.root_writer.RootWriter.flush",
            side_effect=OSError("Disk full"),
        ):
            self.client.post("/api/run/start", json={})
            state = self.idle()
        self.assertEqual(state["state"], "error")
        self.assertEqual(state["status"]["state"], "error")
        self.assertIn("Disk full", state["error"])


class DataPathTests(unittest.TestCase):
    def test_failed_root_extend_is_not_retried_by_cleanup(self):
        writer = RootWriter(Path("unused.root"))
        writer.tree = Mock()
        writer.file = Mock()
        writer.tree.extend.side_effect = OSError("Disk full")
        writer.pending = [Event(0, 0, 0, np.array([1, 2], dtype=np.uint16), 1)]
        extend = writer.tree.extend
        with self.assertRaises(OSError):
            writer.flush()
        self.assertEqual(writer.close(), 0)
        extend.assert_called_once()

    def test_display_envelope_preserves_single_sample_pulse(self):
        values = np.zeros(196608, dtype=np.uint16)
        values[12345] = 4000
        preview = waveform_payload(Event(4, 0, 2**60, values, 1))
        self.assertIn(4000, preview["y"])
        self.assertLessEqual(len(preview["y"]), 2049)
        self.assertEqual(preview["timestamp"], str(2**60))

    def test_display_envelope_keeps_the_incomplete_tail_block(self):
        values = np.zeros(150013, dtype=np.uint16)
        values[-2] = 4095
        preview = waveform_payload(Event(0, 0, 0, values, 1))
        self.assertIn(4095, preview["y"])
        self.assertLessEqual(len(preview["y"]), 2049)

    def test_external_routes_only_trg_in_and_reads_back_level(self):
        config = load_config(
            "configs/channel0_generator_test.yaml", trigger_mode="external"
        )
        config.data["external"]["io_level"] = "TTL"
        board = Mock()
        board.handle = 9

        def read(handle, pointer):
            ct.cast(pointer, ct.POINTER(ct.c_int))[0] = 1
            return 0

        board.get_ext_trigger_fn.side_effect = read
        board.get_io_level_fn.side_effect = read
        configure_triggers(board, config)
        board.self_trigger_fn.assert_called_once_with(9, 0, 15)
        board.sw_trigger_mode_fn.assert_called_once_with(9, 0)
        board.ext_trigger_fn.assert_called_once_with(9, 1)
        board.io_level_fn.assert_called_once_with(9, 1)
        config.data["external"]["polarity"] = "falling"
        with self.assertRaises(ValueError):
            configure_triggers(board, config)

    def test_nonfinite_config_numbers_are_rejected(self):
        config = load_config("configs/channel0_generator_test.yaml")
        for value in (float("nan"), float("inf"), True):
            config.data["periodic"]["interval_s"] = value
            with self.assertRaises(ValueError):
                validate_config(config.data, config.source)


if __name__ == "__main__":
    unittest.main()

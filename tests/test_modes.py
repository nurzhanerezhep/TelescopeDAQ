from __future__ import annotations

import copy
import tempfile
import threading
import time
import yaml
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from telescopedaq.acquisition import Acquisition
from telescopedaq.config import DAQConfig, load_config
from telescopedaq.event import Event
from telescopedaq.root_viewer import load_root_summary, load_waveform
from telescopedaq.threshold_test import run_threshold_scan, threshold_values


class FakeDigitizer:
    latest: "FakeDigitizer | None" = None

    def __init__(self) -> None:
        FakeDigitizer.latest = self
        self.sent_triggers = 0
        self.next_id = 0
        self.periodic = False
        self.empty_read_done = False
        self.threshold_history: list[int] = []

    def open(self, _config: DAQConfig) -> dict[str, object]:
        return {"model": "DT5740D", "serial_number": 1, "adc_bits": 12}

    def reset(self) -> None: pass
    def configure(self, config: DAQConfig) -> None:
        self.periodic = config.trigger["mode"] == "periodic"
    def start(self) -> None: pass
    def stop(self) -> None: pass
    def close(self) -> None: pass
    def set_threshold(self, _channel: int, value_adc: int) -> None:
        self.threshold_history.append(value_adc)
    def clear_data(self) -> None: pass

    def read_events(self) -> list[Event]:
        if self.periodic and not self.empty_read_done:
            self.empty_read_done = True
            time.sleep(0.003)
            return []
        event = Event(
            self.next_id, 0, self.next_id * 10,
            np.asarray([100, 100, 80], dtype=np.uint16), 1,
        )
        self.next_id += 1
        return [event]

    def send_software_trigger(self) -> None:
        self.sent_triggers += 1


class MultiChannelFakeDigitizer(FakeDigitizer):
    def read_events(self) -> list[Event]:
        event_id = self.next_id
        self.next_id += 1
        return [
            Event(event_id, channel, event_id * 10, np.asarray([100, 90], dtype=np.uint16), 1)
            for channel in (0, 1)
        ]


class AcquisitionModeTests(unittest.TestCase):
    def _config(self, output: Path, mode: str) -> DAQConfig:
        original = load_config("configs/channel0_generator_test.yaml", daq_mode=mode)
        data = copy.deepcopy(original.data)
        data["run"]["output_dir"] = str(output)
        data["run"]["max_events"] = 3
        data["monitor"]["enabled"] = False
        return DAQConfig(source=original.source, data=data)

    def test_modes_write_identical_root_and_write_only_has_no_event_callback(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            output = Path(temporary)
            callbacks: dict[str, int] = {}
            for run_id, mode in enumerate(("full_monitor", "write_only"), start=1):
                config = self._config(output, mode)
                config.data["run"]["run_id"] = run_id
                received: list[list[Event]] = []
                with patch("telescopedaq.acquisition.CAENDigitizer", FakeDigitizer):
                    path = Acquisition(config, event_sink=received.append, stop_event=threading.Event()).run()
                callbacks[mode] = len(received)
                summary = load_root_summary(path)
                self.assertEqual(summary.count, 3)
                self.assertEqual(load_waveform(path, 0).tolist(), [100, 100, 80])

            self.assertGreater(callbacks["full_monitor"], 0)
            self.assertEqual(callbacks["write_only"], 0)

    def test_periodic_mode_sends_software_trigger(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            config = self._config(Path(temporary), "write_only")
            config.data["trigger"]["mode"] = "periodic"
            config.data["periodic"].update(enabled=True, interval_s=0.001)
            config.data["threshold"]["enabled"] = False
            config.data["run"]["max_events"] = 1
            with patch("telescopedaq.acquisition.CAENDigitizer", FakeDigitizer):
                Acquisition(config).run()
            assert FakeDigitizer.latest is not None
            self.assertGreaterEqual(FakeDigitizer.latest.sent_triggers, 1)

    def test_failed_display_consumer_does_not_stop_root_recording(self):
        def broken(events):
            raise RuntimeError("Display unavailable")
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            config = self._config(Path(temporary), "full_monitor")
            with patch("telescopedaq.acquisition.CAENDigitizer", FakeDigitizer):
                with self.assertLogs("telescopedaq.acquisition", level="ERROR"):
                    path = Acquisition(config, event_sink=broken, status_sink=broken).run()
            self.assertEqual(load_root_summary(path).count, 3)

    def test_invalid_monitor_interval_is_rejected(self) -> None:
        original = load_config("configs/channel0_generator_test.yaml")
        data = copy.deepcopy(original.data)
        data["monitor"]["update_every_events"] = 0
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            path = Path(temporary) / "invalid.yaml"
            with path.open("w", encoding="utf-8") as stream:
                yaml.safe_dump(data, stream, sort_keys=False)
            with self.assertRaisesRegex(ValueError, "update_every_events"):
                load_config(path)

    def test_invalid_waveform_display_interval_is_rejected(self) -> None:
        original = load_config("configs/channel0_generator_test.yaml")
        data = copy.deepcopy(original.data)
        data["monitor"]["waveform_update_interval_s"] = 0.01
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            path = Path(temporary) / "invalid_display.yaml"
            with path.open("w", encoding="utf-8") as stream:
                yaml.safe_dump(data, stream, sort_keys=False)
            with self.assertRaisesRegex(ValueError, "waveform_update_interval_s"):
                load_config(path)

    def test_fast_batches_do_not_flood_status_sink(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            config = self._config(Path(temporary), "write_only")
            statuses = []
            with patch("telescopedaq.acquisition.CAENDigitizer", FakeDigitizer):
                Acquisition(config, max_events=3, status_sink=statuses.append).run()
            self.assertEqual([status.state for status in statuses], ["running", "stopped"])
            self.assertEqual(statuses[-1].interval_events, 3)

    def test_threshold_scan_reads_each_point_without_writing_root(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            output = Path(temporary)
            config = self._config(output, "full_monitor")
            config.data["trigger"]["mode"] = "threshold"
            config.data["threshold"]["enabled"] = True
            config.data["periodic"]["enabled"] = False
            progress = []
            with patch("telescopedaq.threshold_test.CAENDigitizer", FakeDigitizer):
                result = run_threshold_scan(config, 100, 125, 10, 1.0, 2, threading.Event(), progress.append)
            self.assertEqual(result.thresholds, (100, 110, 120, 125))
            self.assertEqual(result.counts, (2, 2, 2, 2))
            self.assertTrue(result.finished)
            self.assertIsNotNone(result.last_event)
            assert FakeDigitizer.latest is not None
            self.assertEqual(FakeDigitizer.latest.threshold_history, [100, 110, 120, 125])
            self.assertFalse(list(output.glob("*.root")))

    def test_threshold_scan_range_validation(self) -> None:
        self.assertEqual(threshold_values(100, 125, 10), [100, 110, 120, 125])
        with self.assertRaises(ValueError):
            threshold_values(200, 100, 10)
        with self.assertRaises(ValueError):
            threshold_values(0, 100, 0)

    def test_multichannel_waveforms_share_physical_event_count(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            config = self._config(Path(temporary), "full_monitor")
            statuses = []
            with patch("telescopedaq.acquisition.CAENDigitizer", MultiChannelFakeDigitizer):
                path = Acquisition(config, max_events=3, status_sink=statuses.append).run()
            summary = load_root_summary(path)
            self.assertEqual(summary.count, 6)
            self.assertEqual(statuses[-1].total_events, 3)
            self.assertEqual(statuses[-1].written_events, 6)


if __name__ == "__main__":
    unittest.main()

"""Explicitly opt-in synthetic source for UI testing, never an automatic fallback."""

from __future__ import annotations

import time
import numpy as np

from ..event import Event
from ..triggers import TRIGGER_CODES


class DemoDigitizer:
    def __init__(self):
        self.is_open = False
        self.is_running = False
        self.board_info = {
            "model": "DT5740D (DEMO)",
            "serial_number": "SIMULATED",
            "adc_bits": 12,
            "roc_firmware": "Synthetic",
            "hardware_groups": 4,
        }
        self.counter = 0
        self.pending_triggers = 0

    def open(self, config):
        self.is_open = True
        return self.board_info

    def reset(self):
        self.counter = 0

    def configure(self, config):
        self.config = config
        self.threshold = config.data["threshold"]["value_adc"]
        x = np.arange(config.caen["record_length_samples"])
        n = len(x)
        self.waveforms = {
            ch: np.clip(
                2050
                + 4 * np.sin(x / 23 + ch)
                + (1500 if ch == 0 else 25)
                * np.exp(-(((x - 0.68 * n) / (max(n * 0.025, 1))) ** 2)),
                0,
                4095,
            ).astype(np.uint16)
            for ch in config.channels["enabled"]
        }

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False

    def close(self):
        self.stop()
        self.is_open = False

    def clear_data(self):
        self.pending_triggers = 0

    def set_threshold(self, channel, value_adc):
        self.threshold = value_adc

    def send_software_trigger(self):
        self.pending_triggers += 1

    def read_events(self):
        time.sleep(0.02)
        mode = self.config.trigger["mode"]
        if mode == "external":
            return []  # Demo never pretends to validate a physical TRG-IN signal.
        if mode == "periodic":
            count = min(self.pending_triggers, 1)
            self.pending_triggers -= count
        else:
            count = 4 if 2055 < self.threshold < 3500 else 0
        result = []
        for _ in range(count):
            for channel, waveform in self.waveforms.items():
                result.append(
                    Event(
                        self.counter,
                        channel,
                        self.counter * 312500,
                        waveform.copy(),
                        TRIGGER_CODES[mode],
                    )
                )
            self.counter += 1
        return result

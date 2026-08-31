from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class Event:
    event_id: int
    channel: int
    timestamp: int
    waveform: np.ndarray
    trigger_type: int
    polarity: str = "negative"
    baseline_samples: int = 100

    @property
    def baseline(self) -> float:
        n = min(self.baseline_samples, len(self.waveform))
        return float(np.mean(self.waveform[:n], dtype=np.float64)) if n else 0.0

    @property
    def amplitude(self) -> float:
        if not len(self.waveform):
            return 0.0
        if self.polarity == "negative":
            return self.baseline - float(np.min(self.waveform))
        return float(np.max(self.waveform)) - self.baseline

    @property
    def charge(self) -> float:
        samples = self.waveform.astype(np.float64, copy=False)
        if self.polarity == "negative":
            return float(np.sum(self.baseline - samples))
        return float(np.sum(samples - self.baseline))

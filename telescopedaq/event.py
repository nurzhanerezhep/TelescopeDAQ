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

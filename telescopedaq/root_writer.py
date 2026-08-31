from __future__ import annotations

from pathlib import Path

import awkward as ak
import numpy as np
import uproot

from .event import Event


class RootWriter:
    def __init__(self, filename: Path, compression: str = "zlib") -> None:
        self.filename = filename
        self.compression = compression
        self.file: uproot.WritableDirectory | None = None
        self.tree = None

    def open(self) -> None:
        codec = uproot.ZLIB(4) if self.compression.lower() == "zlib" else None
        self.file = uproot.recreate(self.filename, compression=codec)
        self.tree = self.file.mktree("events", {
            "event_id": "uint64", "channel": "uint16", "timestamp": "uint64",
            "trigger_type": "uint16", "baseline": "float32", "amplitude": "float32",
            "charge": "float32", "waveform": "var * uint16",
        })

    def write_events(self, events: list[Event]) -> None:
        if not events:
            return
        if self.tree is None:
            raise RuntimeError("ROOT writer не открыт")
        self.tree.extend({
            "event_id": np.asarray([e.event_id for e in events], dtype=np.uint64),
            "channel": np.asarray([e.channel for e in events], dtype=np.uint16),
            "timestamp": np.asarray([e.timestamp for e in events], dtype=np.uint64),
            "trigger_type": np.asarray([e.trigger_type for e in events], dtype=np.uint16),
            "baseline": np.asarray([e.baseline for e in events], dtype=np.float32),
            "amplitude": np.asarray([e.amplitude for e in events], dtype=np.float32),
            "charge": np.asarray([e.charge for e in events], dtype=np.float32),
            "waveform": ak.Array([e.waveform for e in events]),
        })

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
            self.tree = None

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import awkward as ak
import numpy as np
import uproot


@dataclass(frozen=True, slots=True)
class RootSummary:
    path: Path
    event_ids: np.ndarray
    timestamps: np.ndarray

    @property
    def count(self) -> int:
        return len(self.event_ids)

    def rate_series(self, bins: int = 100) -> tuple[np.ndarray, np.ndarray]:
        if self.count < 2:
            return np.asarray([0.0]), np.asarray([self.count])
        relative = self.timestamps.astype(np.float64) - float(self.timestamps[0])
        counts, edges = np.histogram(relative, bins=min(bins, self.count))
        return (edges[:-1] + edges[1:]) / 2.0, counts


def load_root_summary(path: str | Path) -> RootSummary:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"ROOT-файл не найден: {source}")
    with uproot.open(source) as root:
        if "events" not in root:
            raise ValueError("В ROOT-файле отсутствует дерево events")
        arrays = root["events"].arrays(["event_id", "timestamp"], library="np")
    return RootSummary(
        path=source,
        event_ids=np.asarray(arrays["event_id"]),
        timestamps=np.asarray(arrays["timestamp"]),
    )


def load_waveform(path: str | Path, entry: int) -> np.ndarray:
    with uproot.open(path) as root:
        tree = root["events"]
        if not 0 <= entry < tree.num_entries:
            raise IndexError(f"Событие {entry} вне диапазона 0..{tree.num_entries - 1}")
        waveforms = tree["waveform"].array(entry_start=entry, entry_stop=entry + 1)
    return np.asarray(ak.to_numpy(waveforms[0]), dtype=np.uint16)

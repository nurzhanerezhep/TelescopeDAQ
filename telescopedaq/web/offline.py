"""Bounded-memory ROOT browsing, independent of hardware ownership."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import uproot

from ..event import Event
from .service import waveform_payload

REQUIRED = {"event_id", "channel", "timestamp", "trigger_type", "waveform"}


def check_tree(root):
    if "events" not in root or not REQUIRED.issubset(root["events"].keys()):
        raise ValueError(
            "ROOT requires an events tree with event_id, channel, timestamp, trigger_type, waveform"
        )
    return root["events"]


def summary(path: Path):
    stat = path.stat()
    return _summary(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4)
def _summary(path: str, modified: int, size: int):
    with uproot.open(path) as root:
        tree = check_tree(root)
        counts = np.zeros(16, dtype=np.int64)
        triggers = 0
        first = last = previous_id = None
        for batch in tree.iterate(
            ["event_id", "channel", "timestamp"], step_size="8 MB", library="np"
        ):
            ids = batch["event_id"]
            if not len(ids):
                continue
            unique = np.concatenate(
                ([previous_id is None or ids[0] != previous_id], ids[1:] != ids[:-1])
            )
            stamps = batch["timestamp"][unique]
            triggers += int(unique.sum())
            if len(stamps):
                if first is None:
                    first = int(stamps[0])
                last = int(stamps[-1])
            previous_id = int(ids[-1])
            channels = batch["channel"]
            counts += np.bincount(channels[channels < 16], minlength=16)
        first = first or 0
        last = max(last or first, first + 1)
        edges = np.linspace(0, last - first, 101)
        histogram = np.zeros(100, dtype=np.int64)
        previous_id = None
        for batch in tree.iterate(
            ["event_id", "timestamp"], step_size="8 MB", library="np"
        ):
            ids = batch["event_id"]
            if not len(ids):
                continue
            mask = np.concatenate(
                ([previous_id is None or ids[0] != previous_id], ids[1:] != ids[:-1])
            )
            histogram += np.histogram(
                (batch["timestamp"][mask] - np.uint64(first)).astype(np.float64),
                bins=edges,
            )[0]
            previous_id = int(ids[-1])
        return {
            "name": Path(path).name,
            "rows": tree.num_entries,
            "events": triggers,
            "bytes": size,
            "channels": counts.tolist(),
            "first_timestamp": str(first),
            "rate_x": ((edges[1:] + edges[:-1]) / 2).tolist(),
            "rate_y": histogram.tolist(),
        }


def waveform(path: Path, entry: int):
    with uproot.open(path) as root:
        tree = check_tree(root)
        if not 0 <= entry < tree.num_entries:
            raise ValueError(f"Entry must be in 0..{tree.num_entries - 1}")
        batch = tree.arrays(
            list(REQUIRED), entry_start=entry, entry_stop=entry + 1, library="ak"
        )
        event = Event(
            int(batch.event_id[0]),
            int(batch.channel[0]),
            int(batch.timestamp[0]),
            np.asarray(batch.waveform[0], dtype=np.uint16),
            int(batch.trigger_type[0]),
        )
        return waveform_payload(event)

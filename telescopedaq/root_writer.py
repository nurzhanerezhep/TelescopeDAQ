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
        self.pending: list[Event] = []
        self.pending_bytes = 0
        self.written_count = 0
        self.failed = False

    def open(self) -> None:
        codec = uproot.ZLIB(4) if self.compression.lower() == "zlib" else None
        self.file = uproot.create(self.filename, compression=codec)
        self.tree = self.file.mktree(
            "events",
            {
                "event_id": "uint64",
                "channel": "uint16",
                "timestamp": "uint64",
                "trigger_type": "uint16",
                "waveform": "var * uint16",
            },
        )

    def write_events(self, events: list[Event]) -> int:
        if self.failed:
            raise RuntimeError("ROOT writer is failed; start a new run")
        if not events:
            return 0
        self.pending.extend(events)
        self.pending_bytes += sum(event.waveform.nbytes for event in events)
        if len(self.pending) < 1024 and self.pending_bytes < 16 * 1024 * 1024:
            return 0
        return self.flush()

    def flush(self) -> int:
        if self.failed:
            raise RuntimeError(
                "ROOT writer is failed; refusing to retry a possibly partial write"
            )
        events = self.pending
        if not events:
            return 0
        if self.tree is None:
            raise RuntimeError("ROOT writer не открыт")
        lengths = np.fromiter(
            (e.waveform.size for e in events), dtype=np.int64, count=len(events)
        )
        offsets = np.concatenate(([0], np.cumsum(lengths)))
        # Build ragged arrays from contiguous buffers, avoiding Python per-sample conversion.
        waveforms = ak.Array(
            ak.contents.ListOffsetArray(
                ak.index.Index64(offsets),
                ak.contents.NumpyArray(np.concatenate([e.waveform for e in events])),
            )
        )
        payload = {
            "event_id": np.asarray([e.event_id for e in events], dtype=np.uint64),
            "channel": np.asarray([e.channel for e in events], dtype=np.uint16),
            "timestamp": np.asarray([e.timestamp for e in events], dtype=np.uint64),
            "trigger_type": np.asarray(
                [e.trigger_type for e in events], dtype=np.uint16
            ),
            "waveform": waveforms,
        }
        try:
            self.tree.extend(payload)
        except Exception:
            # An extend can fail after partial disk I/O; retrying could duplicate rows.
            self.failed = True
            raise
        self.written_count += len(events)
        self.pending = []
        self.pending_bytes = 0
        return len(events)

    def close(self) -> int:
        try:
            return self.flush() if self.tree is not None and not self.failed else 0
        finally:
            if self.file is not None:
                self.file.close()
                self.file = None
                self.tree = None

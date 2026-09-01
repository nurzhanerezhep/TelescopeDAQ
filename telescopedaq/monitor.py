from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path

from .event import Event

LOG = logging.getLogger(__name__)


class OnlineMonitor:
    def __init__(self, run_id: int, update_every: int, output: Path) -> None:
        self.run_id, self.update_every, self.output = run_id, update_every, output
        self.started = time.monotonic()
        self.total = 0
        self.rate_window: deque[tuple[float, int]] = deque()

    def update(self, events: list[Event]) -> None:
        if not events:
            return
        trigger_count = len({event.event_id for event in events})
        self.total += trigger_count
        now = time.monotonic()
        self.rate_window.append((now, trigger_count))
        while self.rate_window and self.rate_window[0][0] < now - 10.0:
            self.rate_window.popleft()
        if self.total % self.update_every >= trigger_count and self.total != trigger_count:
            return
        last = events[-1]
        elapsed = max(now - self.started, 1e-9)
        rate_span = min(elapsed, 10.0)
        rate = sum(count for _, count in self.rate_window) / rate_span
        size = self.output.stat().st_size / 1048576 if self.output.exists() else 0.0
        LOG.info(
            "run=%06d events=%d elapsed=%.1fs readout_rate=%.1fHz ch=%d timestamp=%d file=%.2fMiB",
            self.run_id, self.total, elapsed, rate, last.channel,
            last.timestamp, size,
        )

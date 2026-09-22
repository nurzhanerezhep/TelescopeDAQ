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
        self.window_count = 0
        self.last_log = 0.0
        self.last_logged_count = 0

    def update(self, events: list[Event], trigger_count: int | None = None) -> None:
        if not events:
            return
        if trigger_count is None:
            trigger_count = len({event.event_id for event in events})
        self.total += trigger_count
        now = time.monotonic()
        self.rate_window.append((now, trigger_count))
        self.window_count += trigger_count
        while self.rate_window and self.rate_window[0][0] < now - 10.0:
            self.window_count -= self.rate_window.popleft()[1]
        if now - self.last_log < 1.0 or (
            self.last_logged_count
            and self.total - self.last_logged_count < self.update_every
        ):
            return
        self.last_log, self.last_logged_count = now, self.total
        last = events[-1]
        elapsed = max(now - self.started, 1e-9)
        rate_span = min(elapsed, 10.0)
        rate = self.window_count / rate_span
        size = self.output.stat().st_size / 1048576 if self.output.exists() else 0.0
        LOG.info(
            "run=%06d events=%d elapsed=%.1fs readout_rate=%.1fHz ch=%d timestamp=%d file=%.2fMiB",
            self.run_id,
            self.total,
            elapsed,
            rate,
            last.channel,
            last.timestamp,
            size,
        )

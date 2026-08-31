from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .event import Event


class OnlineMonitor:
    def __init__(self, run_id: int, update_every: int, output: Path) -> None:
        self.run_id, self.update_every, self.output = run_id, update_every, output
        self.started = time.monotonic()
        self.total = 0
        self.console = Console()

    def update(self, events: list[Event]) -> None:
        if not events:
            return
        self.total += len(events)
        if self.total % self.update_every >= len(events) and self.total != len(events):
            return
        last = events[-1]
        elapsed = max(time.monotonic() - self.started, 1e-9)
        table = Table(title=f"TelescopeDAQ run {self.run_id}")
        for column in ("events", "elapsed, s", "rate, Hz", "ch", "timestamp", "baseline", "amplitude", "charge", "file, MiB"):
            table.add_column(column)
        size = self.output.stat().st_size / 1048576 if self.output.exists() else 0.0
        table.add_row(str(self.total), f"{elapsed:.1f}", f"{self.total/elapsed:.1f}", str(last.channel), str(last.timestamp), f"{last.baseline:.2f}", f"{last.amplitude:.2f}", f"{last.charge:.1f}", f"{size:.2f}")
        self.console.print(table)

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .acquisition import Acquisition
from .config import load_config
from .event import Event


def start_run(
    config_path: str | Path,
    max_events: int | None = None,
    event_sink: Callable[[list[Event]], None] | None = None,
    board_sink: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
) -> Path:
    config = load_config(config_path)
    logs = Path("logs")
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[logging.FileHandler(logs / f"run_{int(config.run['run_id']):06d}.log", encoding="utf-8"), logging.StreamHandler()])
    return Acquisition(
        config,
        max_events=max_events,
        event_sink=event_sink,
        board_sink=board_sink,
        stop_event=stop_event,
    ).run()

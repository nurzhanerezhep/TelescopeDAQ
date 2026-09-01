from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .acquisition import Acquisition, AcquisitionStatus
from .config import load_config
from .event import Event


def start_run(
    config_path: str | Path,
    max_events: int | None = None,
    event_sink: Callable[[list[Event]], None] | None = None,
    board_sink: Callable[[str], None] | None = None,
    status_sink: Callable[[AcquisitionStatus], None] | None = None,
    stop_event: threading.Event | None = None,
    daq_mode: str | None = None,
    trigger_mode: str | None = None,
) -> Path:
    config = load_config(config_path, daq_mode=daq_mode, trigger_mode=trigger_mode)
    logs = Path("logs")
    logs.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.FileHandler(logs / f"run_{int(config.run['run_id']):06d}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    if not any(type(handler) is logging.StreamHandler for handler in root_logger.handlers):
        terminal_handler = logging.StreamHandler()
        terminal_handler.setFormatter(formatter)
        root_logger.addHandler(terminal_handler)
    try:
        return Acquisition(
            config,
            max_events=max_events,
            event_sink=event_sink,
            board_sink=board_sink,
            status_sink=status_sink,
            stop_event=stop_event,
        ).run()
    finally:
        root_logger.removeHandler(file_handler)
        file_handler.close()

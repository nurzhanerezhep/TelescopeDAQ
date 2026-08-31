from __future__ import annotations

import logging
from pathlib import Path

from .acquisition import Acquisition
from .config import load_config


def start_run(config_path: str | Path, max_events: int | None = None) -> Path:
    config = load_config(config_path)
    logs = Path("logs")
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[logging.FileHandler(logs / f"run_{int(config.run['run_id']):06d}.log", encoding="utf-8"), logging.StreamHandler()])
    return Acquisition(config, max_events=max_events).run()

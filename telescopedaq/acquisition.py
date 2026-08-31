from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .caen_digitizer import CAENDigitizer
from .config import DAQConfig
from .monitor import OnlineMonitor
from .root_writer import RootWriter
from .utils import stop_key_pressed

LOG = logging.getLogger(__name__)


class Acquisition:
    def __init__(self, config: DAQConfig, max_events: int | None = None) -> None:
        self.config = config
        self.max_events = max_events or int(config.run["max_events"])
        run_id = int(config.run["run_id"])
        self.output_dir = Path(config.run["output_dir"])
        self.root_path = self.output_dir / f"run_{run_id:06d}.root"
        self.config_copy = self.output_dir / f"run_{run_id:06d}_config.yaml"

    def run(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.config.source, self.config_copy)
        digitizer = CAENDigitizer()
        writer = RootWriter(self.root_path, self.config.data["storage"].get("compression", "zlib"))
        monitor = OnlineMonitor(int(self.config.run["run_id"]), int(self.config.data["monitor"]["update_every_events"]), self.root_path)
        total = 0
        try:
            board = digitizer.open(self.config)
            LOG.info("CAEN board: %s", board)
            print(f"Подключена плата: {board}")
            digitizer.reset()
            digitizer.configure(self.config)
            writer.open()
            digitizer.start()
            print("Сбор запущен. Для безопасной остановки нажмите Ctrl+C, Enter или Esc.")
            while total < self.max_events:
                if stop_key_pressed():
                    print("Получена команда остановки с клавиатуры.")
                    break
                events = digitizer.read_events()
                if events:
                    events = events[: self.max_events - total]
                    writer.write_events(events)
                    monitor.update(events)
                    total += len(events)
                else:
                    time.sleep(0.005)
        except KeyboardInterrupt:
            print("Остановка по Ctrl+C.")
        finally:
            # Each cleanup step is isolated so one hardware error cannot prevent
            # the ROOT file or the USB handle from being closed.
            try:
                digitizer.stop()
            except Exception:
                LOG.exception("Не удалось остановить acquisition")
            try:
                writer.close()
            except Exception:
                LOG.exception("Не удалось закрыть ROOT-файл")
            digitizer.close()
            LOG.info("Run закрыт, сохранено событий: %d", total)
        print(f"Сохранено событий: {total}; ROOT: {self.root_path.resolve()}")
        return self.root_path

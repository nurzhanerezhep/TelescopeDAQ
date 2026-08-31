from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .caen_digitizer import CAENDigitizer
from .config import DAQConfig
from .event import Event
from .monitor import OnlineMonitor
from .root_writer import RootWriter
from .utils import stop_key_pressed

LOG = logging.getLogger(__name__)


class Acquisition:
    def __init__(
        self,
        config: DAQConfig,
        max_events: int | None = None,
        event_sink: Callable[[list[Event]], None] | None = None,
        board_sink: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.max_events = max_events or int(config.run["max_events"])
        self.event_sink = event_sink
        self.board_sink = board_sink
        self.stop_event = stop_event
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
            if self.board_sink is not None:
                self.board_sink(
                    f"{board['model']} · S/N {board['serial_number']} · {board['adc_bits']} bit"
                )
            digitizer.reset()
            digitizer.configure(self.config)
            writer.open()
            digitizer.start()
            print("Сбор запущен. Для безопасной остановки нажмите Ctrl+C, Enter или Esc.")
            while total < self.max_events:
                if self.stop_event is not None and self.stop_event.is_set():
                    LOG.info("Run stopped by external request")
                    break
                if stop_key_pressed():
                    print("Получена команда остановки с клавиатуры.")
                    break
                events = digitizer.read_events()
                if events:
                    events = events[: self.max_events - total]
                    writer.write_events(events)
                    monitor.update(events)
                    if self.event_sink is not None:
                        self.event_sink(events)
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

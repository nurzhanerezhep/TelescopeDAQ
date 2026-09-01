from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .caen_digitizer import CAENDigitizer
from .config import DAQConfig
from .event import Event
from .monitor import OnlineMonitor
from .root_writer import RootWriter
from .utils import stop_key_pressed

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AcquisitionStatus:
    run_id: int
    elapsed_s: float
    total_events: int
    event_rate_hz: float
    interval_events: int
    rate_interval_s: float
    written_events: int
    root_size_bytes: int
    disk_free_bytes: int
    last_timestamp: int | None
    caen_errors: int
    state: str


class Acquisition:
    def __init__(
        self,
        config: DAQConfig,
        max_events: int | None = None,
        event_sink: Callable[[list[Event]], None] | None = None,
        board_sink: Callable[[str], None] | None = None,
        status_sink: Callable[[AcquisitionStatus], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.max_events = int(config.run["max_events"]) if max_events is None else max_events
        self.event_sink = event_sink
        self.board_sink = board_sink
        self.status_sink = status_sink
        self.stop_event = stop_event
        self.run_id = int(config.run["run_id"])
        self.output_dir = Path(config.run["output_dir"])
        self.root_path = self.output_dir / f"run_{self.run_id:06d}.root"
        self.config_copy = self.output_dir / f"run_{self.run_id:06d}_config.yaml"

    def _publish_status(
        self,
        started: float,
        total: int,
        written: int,
        last_timestamp: int | None,
        errors: int,
        state: str,
        rate_hz: float,
        interval_events: int = 0,
    ) -> None:
        if self.status_sink is None:
            return
        elapsed = max(time.monotonic() - started, 0.0)
        self.status_sink(AcquisitionStatus(
            run_id=self.run_id,
            elapsed_s=elapsed,
            total_events=total,
            event_rate_hz=rate_hz,
            interval_events=interval_events,
            rate_interval_s=float(self.config.data["monitor"].get("rate_interval_s", 1.0)),
            written_events=written,
            root_size_bytes=self.root_path.stat().st_size if self.root_path.exists() else 0,
            disk_free_bytes=shutil.disk_usage(self.output_dir).free,
            last_timestamp=last_timestamp,
            caen_errors=errors,
            state=state,
        ))

    def run(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.config_copy.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(self.config.data, stream, sort_keys=False, allow_unicode=True)
        digitizer = CAENDigitizer()
        writer = RootWriter(self.root_path, self.config.data["storage"].get("compression", "zlib"))
        monitor = OnlineMonitor(int(self.config.run["run_id"]), int(self.config.data["monitor"]["update_every_events"]), self.root_path)
        total = 0
        written = 0
        last_timestamp: int | None = None
        errors = 0
        started = time.monotonic()
        last_status = started
        rate_window: deque[tuple[float, int]] = deque()
        rate_interval_s = float(self.config.data["monitor"].get("rate_interval_s", 1.0))
        rate_hz = 0.0
        interval_events = 0
        trigger_mode = self.config.trigger["mode"]
        next_periodic = started + float(self.config.data["periodic"]["interval_s"])
        try:
            board = digitizer.open(self.config)
            LOG.info("CAEN board: %s", board)
            if self.board_sink is not None:
                self.board_sink(
                    f"{board['model']} · S/N {board['serial_number']} · {board['adc_bits']} bit"
                )
            digitizer.reset()
            digitizer.configure(self.config)
            writer.open()
            digitizer.start()
            self._publish_status(started, total, written, last_timestamp, errors, "running", 0.0)
            LOG.info("Сбор запущен; безопасная остановка: Stop, Ctrl+C, Enter или Esc")
            while total < self.max_events:
                if self.stop_event is not None and self.stop_event.is_set():
                    LOG.info("Run stopped by external request")
                    break
                if stop_key_pressed():
                    LOG.info("Получена команда остановки с клавиатуры")
                    break
                now = time.monotonic()
                if trigger_mode == "periodic" and now >= next_periodic:
                    digitizer.send_software_trigger()
                    interval = float(self.config.data["periodic"]["interval_s"])
                    next_periodic += interval * max(1, int((now - next_periodic) // interval) + 1)
                events = digitizer.read_events()
                if events:
                    event_ids = list(dict.fromkeys(event.event_id for event in events))
                    accepted_ids = set(event_ids[: self.max_events - total])
                    events = [event for event in events if event.event_id in accepted_ids]
                    trigger_count = len(accepted_ids)
                    written += writer.write_events(events)
                    total += trigger_count
                    last_timestamp = events[-1].timestamp
                    rate_window.append((now, trigger_count))
                    if self.config.data["monitor"]["enabled"]:
                        monitor.update(events)
                    if self.config.daq["mode"] == "full_monitor" and self.event_sink is not None:
                        latest_by_channel = {event.channel: event for event in events}
                        self.event_sink(list(latest_by_channel.values()))
                else:
                    time.sleep(0.005)
                while rate_window and rate_window[0][0] < now - rate_interval_s:
                    rate_window.popleft()
                interval_events = sum(count for _, count in rate_window)
                observed_s = min(max(now - started, 1e-9), rate_interval_s)
                rate_hz = interval_events / observed_s
                if now - last_status >= 1.0:
                    self._publish_status(started, total, written, last_timestamp, errors, "running", rate_hz, interval_events)
                    last_status = now
        except KeyboardInterrupt:
            LOG.info("Остановка по Ctrl+C")
        except Exception:
            errors += 1
            self._publish_status(started, total, written, last_timestamp, errors, "error", 0.0)
            raise
        finally:
            # Each cleanup step is isolated so one hardware error cannot prevent
            # the ROOT file or the USB handle from being closed.
            try:
                digitizer.stop()
            except Exception:
                errors += 1
                LOG.exception("Не удалось остановить acquisition")
            try:
                written += writer.close()
            except Exception:
                LOG.exception("Не удалось закрыть ROOT-файл")
            digitizer.close()
            LOG.info("Run закрыт, сохранено событий: %d", total)
            self._publish_status(started, total, written, last_timestamp, errors, "stopped", rate_hz, interval_events)
        LOG.info("Сохранено событий: %d; ROOT: %s", total, self.root_path.resolve())
        return self.root_path

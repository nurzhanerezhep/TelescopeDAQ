from __future__ import annotations

import copy
import hashlib
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import yaml

from ..acquisition import Acquisition
from ..caen_digitizer import CAENDigitizer
from ..config import load_config, validate_config
from ..threshold_test import run_threshold_scan, threshold_values
from .demo import DemoDigitizer

LOG = logging.getLogger(__name__)


class BusyError(RuntimeError):
    pass


def waveform_payload(event, max_points=2048):
    """Peak-preserving min/max envelope for the screen; ROOT always gets full samples."""
    samples = event.waveform
    n = len(samples)
    if n <= max_points:
        indices = np.arange(n)
    else:
        width = int(np.ceil(n / ((max_points - 3) // 2)))
        blocks = n // width
        matrix = samples[: blocks * width].reshape(blocks, width)
        base = np.arange(blocks) * width
        tail = samples[blocks * width :]
        tail_indices = (
            []
            if not len(tail)
            else [
                blocks * width + int(tail.argmin()),
                blocks * width + int(tail.argmax()),
            ]
        )
        indices = np.unique(
            np.concatenate(
                (
                    base + matrix.argmin(axis=1),
                    base + matrix.argmax(axis=1),
                    tail_indices,
                    [n - 1],
                )
            )
        ).astype(np.int64)
    return {
        "channel": event.channel,
        "event_id": str(event.event_id),
        "timestamp": str(event.timestamp),
        "samples": n,
        "x": indices.tolist(),
        "y": samples[indices].tolist(),
    }


class MemoryLog(logging.Handler):
    def __init__(self, service):
        super().__init__()
        # emit already uses service.lock; a second handler lock inverts lock ordering.
        self.lock = service.lock
        self.service = service

    def emit(self, record):
        if not record.name.startswith("telescopedaq"):
            return
        with self.service.lock:
            self.service.log_id += 1
            self.service.logs.append(
                {
                    "id": self.service.log_id,
                    "time": time.strftime("%H:%M:%S"),
                    "level": record.levelname,
                    "message": self.format(record),
                }
            )


class DAQService:
    def __init__(self, config_path: Path, workspace: Path, demo=False, factory=None):
        self.workspace = workspace.resolve()
        self.config_path = config_path.resolve()
        self.config = load_config(self.config_path)
        self.demo = demo
        self.factory = factory or (DemoDigitizer if demo else CAENDigitizer)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="caen-owner"
        )
        self.future = None
        self.busy = False
        self.closed = False
        self.shutdown_future = None
        self.shutdown_state = None
        self.shutdown_error = None
        self.close_lock = threading.Lock()
        self.executor_closed = False
        self.device = None
        self.stop_event = threading.Event()
        self.state = "disconnected"
        self.operation = None
        self.error = None
        self.board = None
        self.status = {}
        self.run_config = None
        self.active_path = None
        self.last_output = None
        self.preview = {"sequence": 0, "channels": []}
        self.history = deque(maxlen=1801)
        self.scan = {}
        self.logs = deque(maxlen=1000)
        self.log_id = 0
        self.handler = MemoryLog(self)
        logging.getLogger("telescopedaq").addHandler(self.handler)
        self.revision = self._revision()

    def _revision(self):
        return hashlib.sha256(self.config_path.read_bytes()).hexdigest()

    def local_path(self, value: str | Path) -> Path:
        path = (self.workspace / value).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("Path must stay inside the project workspace")
        return path

    def configuration(self):
        with self.lock:
            if not self.busy and self._revision() != self.revision:
                updated = load_config(self.config_path)
                self.local_path(updated.run["output_dir"])
                if self.device is not None and any(
                    updated.caen[key] != self.config.caen[key]
                    for key in ("link_num", "conet_node", "vme_base_address")
                ):
                    raise BusyError(
                        "Disconnect CAEN before reloading USB link settings"
                    )
                self.config = updated
                self.revision = self._revision()
            return {
                "data": copy.deepcopy(self.config.data),
                "revision": self.revision,
                "path": self.config_path.name,
            }

    def save_config(self, data, revision, save=True):
        config = validate_config(data, self.config_path)
        self.local_path(config.run["output_dir"])
        with self.lock:
            if self.busy or self.closed:
                raise BusyError("Stop the current operation before changing Settings")
            if self.device is not None and any(
                config.caen[key] != self.config.caen[key]
                for key in ("link_num", "conet_node", "vme_base_address")
            ):
                raise BusyError("Disconnect CAEN before changing USB link settings")
            if revision != self.revision or self._revision() != self.revision:
                raise BusyError(
                    "Settings changed in another session. Reload Settings first"
                )
            if save:
                temporary = self.config_path.with_suffix(".yaml.tmp")
                try:
                    temporary.write_text(
                        yaml.safe_dump(
                            config.data, sort_keys=False, allow_unicode=True
                        ),
                        encoding="utf-8",
                    )
                    os.replace(temporary, self.config_path)
                finally:
                    temporary.unlink(missing_ok=True)
                self.config = config
                self.revision = self._revision()
                LOG.info("Settings saved: %s", self.config_path.name)
            return {"data": config.data, "revision": self.revision}

    def _submit(self, operation, work):
        with self.lock:
            if self.busy or self.closed:
                raise BusyError(
                    "CAEN is busy with " + str(self.operation or "shutdown")
                )
            self.busy = True
            self.operation = operation
            self.state = operation
            self.error = None
            self.stop_event.clear()
            self.future = self.executor.submit(self._execute, operation, work)
        return {"accepted": True, "operation": operation}

    def _execute(self, operation, work):
        try:
            work()
            with self.lock:
                if not self.closed:
                    self.state = (
                        "connected" if self.device is not None else "disconnected"
                    )
        except Exception as exc:
            LOG.exception("%s failed", operation)
            try:
                self._close_device()
            except Exception:
                LOG.exception("Cleanup failed")
            with self.lock:
                self.error = str(exc)
                self.state = "error"
                if self.closed:
                    self.shutdown_error = str(exc)
        finally:
            with self.lock:
                self.active_path = None
                self.busy = False
                self.operation = None

    def _close_device(self):
        device, self.device = self.device, None
        if device is not None:
            device.close()

    def connect(self):
        def work():
            self._close_device()
            self.device = self.factory()
            board = self.device.open(self.config)
            with self.lock:
                self.board = board
            LOG.info("CAEN connected: %s", board["model"])

        return self._submit("connecting", work)

    def disconnect(self):
        def work():
            self._close_device()
            LOG.info("CAEN disconnected; USB handle released")

        return self._submit("disconnecting", work)

    def _runtime_config(self, daq_mode, trigger_mode):
        config = validate_config(
            self.config.data, self.config_path, daq_mode, trigger_mode
        )
        output = self.local_path(config.run["output_dir"])
        if self.demo:
            output = self.workspace / "output" / "demo"
        config.run["output_dir"] = str(output)
        return config

    def start(self, daq_mode, trigger_mode):
        with self.lock:
            config = self._runtime_config(daq_mode, trigger_mode)
            if daq_mode == "root_viewer":
                raise ValueError("ROOT Viewer cannot start acquisition")
            target = (
                Path(config.run["output_dir"]) / f"run_{config.run['run_id']:06d}.root"
            )
            if (
                target.exists()
                or target.with_name(target.stem + "_config.yaml").exists()
            ):
                raise FileExistsError(
                    "Run ID already exists. Change Run ID in Settings"
                )

            def work():
                self.device = self.device or self.factory()
                with self.lock:
                    self.run_config = config.data
                    self.status = {}
                    self.history.clear()
                    self.preview = {
                        "sequence": self.preview["sequence"] + 1,
                        "channels": [],
                    }
                    self.active_path = target
                    self.last_output = str(target.relative_to(self.workspace)).replace(
                        "\\", "/"
                    )
                try:
                    Acquisition(
                        config,
                        digitizer=self.device,
                        keyboard_control=False,
                        stop_event=self.stop_event,
                        event_sink=self._events,
                        status_sink=self._status,
                        board_sink=self._board,
                    ).run()
                finally:
                    self._close_device()
                LOG.info("Run finished: %s", target.name)

            return self._submit("acquiring", work)

    def _board(self, text):
        with self.lock:
            self.board = getattr(self.device, "board_info", {"model": text})

    def _status(self, status):
        with self.lock:
            self.status = asdict(status)
            self.status["last_timestamp"] = (
                None if status.last_timestamp is None else str(status.last_timestamp)
            )
            self.history.append([time.time(), status.interval_events])
            if (
                status.state == "running"
                and self.state != "stopping"
                and not self.closed
            ):
                self.state = "running"
        if status.state != "running":
            LOG.info(
                "Run state=%s events=%d written waveforms=%d",
                status.state,
                status.total_events,
                status.written_events,
            )

    def _events(self, events):
        # Serialization/decimation is only performed at the acquisition display holdoff.
        channels = [waveform_payload(event) for event in events]
        with self.lock:
            self.preview = {
                "sequence": self.preview["sequence"] + 1,
                "channels": channels,
            }

    def start_scan(self, lower, upper, step, dwell_s, max_events):
        points = threshold_values(lower, upper, step)
        with self.lock:
            config = self._runtime_config("full_monitor", "threshold")

            def work():
                self.device = self.device or self.factory()
                with self.lock:
                    self.scan = {
                        "thresholds": [],
                        "counts": [],
                        "rates_hz": [],
                        "finished": False,
                        "points": len(points),
                        "current_threshold": lower,
                    }

                def progress(value):
                    with self.lock:
                        self.scan = {
                            "thresholds": list(value.thresholds),
                            "counts": list(value.counts),
                            "rates_hz": list(value.rates_hz),
                            "finished": value.finished,
                            "points": len(points),
                            "current_threshold": value.current_threshold,
                        }

                try:
                    run_threshold_scan(
                        config,
                        lower,
                        upper,
                        step,
                        dwell_s,
                        max_events,
                        self.stop_event,
                        progress,
                        digitizer=self.device,
                    )
                finally:
                    self._close_device()

            return self._submit("scanning", work)

    def stop(self, emergency=False):
        with self.lock:
            if self.busy and self.operation in ("acquiring", "scanning"):
                self.state = "stopping"
                self.stop_event.set()
                LOG.warning(
                    "Emergency stop requested" if emergency else "Stop requested"
                )
            return {"state": self.state}

    def snapshot(self):
        with self.lock:
            return {
                "state": self.state,
                "operation": self.operation,
                "busy": self.busy,
                "closed": self.closed,
                "shutdown_state": self.shutdown_state,
                "shutdown_error": self.shutdown_error,
                "connected": self.device is not None,
                "demo": self.demo,
                "board": self.board,
                "error": self.error,
                "status": dict(self.status),
                "history": list(self.history),
                "scan": copy.deepcopy(self.scan),
                "preview_info": [
                    {key: channel[key] for key in ("channel", "event_id", "samples")}
                    for channel in self.preview["channels"]
                ],
                "run_config": self.run_config,
                "output": self.last_output,
            }

    def request_shutdown(self):
        """Block new work immediately; finish acquisition and cleanup on its owner thread."""
        with self.lock:
            if self.shutdown_future is not None:
                return self.shutdown_future
            self.closed = True
            self.state = "shutting_down"
            self.shutdown_state = "stopping"
            self.stop_event.set()
            self.shutdown_future = self.executor.submit(self._finish_shutdown)
            LOG.info(
                "Safe exit requested; waiting for ROOT finalization and CAEN cleanup"
            )
            return self.shutdown_future

    def _finish_shutdown(self):
        # Queued after the active job, whose finally block closes the ROOT writer.
        try:
            self._close_device()
        except Exception as exc:
            LOG.exception("Safe exit: CAEN cleanup failed")
            with self.lock:
                self.shutdown_error = str(exc)
        with self.lock:
            self.shutdown_state = "error" if self.shutdown_error else "ready"
            self.state = "shutdown_error" if self.shutdown_error else "closed"
        if self.shutdown_error:
            LOG.error("Safe exit could not be confirmed: %s", self.shutdown_error)
        else:
            LOG.info(
                "Safe exit ready: acquisition finished, ROOT closed, CAEN released"
            )

    def close(self):
        with self.close_lock:
            if self.executor_closed:
                return
            self.request_shutdown().result()
            self.executor.shutdown(wait=True)
            self.executor_closed = True
            logging.getLogger("telescopedaq").removeHandler(self.handler)

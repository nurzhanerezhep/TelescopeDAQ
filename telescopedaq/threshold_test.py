from __future__ import annotations

import threading
import time
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

from .caen_digitizer import CAENDigitizer
from .config import DAQConfig
from .event import Event

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ThresholdTestProgress:
    elapsed_s: float
    total_events: int
    event_rate_hz: float
    last_event: Event | None


@dataclass(frozen=True, slots=True)
class ThresholdScanProgress:
    current_threshold: int
    thresholds: tuple[int, ...]
    counts: tuple[int, ...]
    rates_hz: tuple[float, ...]
    last_event: Event | None
    finished: bool = False


def threshold_values(lower_adc: int, upper_adc: int, step_adc: int) -> list[int]:
    if not 0 <= lower_adc <= upper_adc <= 4095:
        raise ValueError("Диапазон threshold должен удовлетворять 0 <= lower <= upper <= 4095")
    if step_adc <= 0:
        raise ValueError("Шаг threshold должен быть больше нуля")
    values = list(range(lower_adc, upper_adc + 1, step_adc))
    if values[-1] != upper_adc:
        values.append(upper_adc)
    if len(values) > 1000:
        raise ValueError("Слишком много точек threshold scan; максимум 1000")
    return values


def run_threshold_scan(
    config: DAQConfig,
    lower_adc: int,
    upper_adc: int,
    step_adc: int,
    dwell_s: float,
    max_events_per_point: int,
    stop_event: threading.Event,
    progress_sink: Callable[[ThresholdScanProgress], None],
    digitizer=None,
) -> ThresholdScanProgress:
    values = threshold_values(lower_adc, upper_adc, step_adc)
    if not math.isfinite(dwell_s) or dwell_s <= 0 or max_events_per_point <= 0:
        raise ValueError("Dwell time и max events per point должны быть больше нуля")
    if config.trigger["mode"] != "threshold":
        raise ValueError("Threshold scan требует trigger.mode: threshold")
    digitizer = digitizer or CAENDigitizer()
    thresholds: list[int] = []
    counts: list[int] = []
    rates: list[float] = []
    last_event: Event | None = None
    current = values[0]
    try:
        config.data["threshold"]["value_adc"] = current
        board = digitizer.board_info if getattr(digitizer, "is_open", False) else digitizer.open(config)
        LOG.info("Threshold scan connected: %s", board)
        digitizer.reset(); digitizer.configure(config)
        for current in values:
            if stop_event.is_set():
                break
            digitizer.stop(); digitizer.set_threshold(0, current); digitizer.clear_data(); digitizer.start()
            point_started = time.monotonic(); point_count = 0
            while not stop_event.is_set() and point_count < max_events_per_point:
                now = time.monotonic()
                if now - point_started >= dwell_s:
                    break
                events = digitizer.read_events()
                if events:
                    event_ids = list(dict.fromkeys(event.event_id for event in events))
                    accepted_ids = set(event_ids[: max_events_per_point - point_count])
                    accepted = [event for event in events if event.event_id in accepted_ids]
                    point_count += len(accepted_ids)
                    last_event = next((event for event in reversed(accepted) if event.channel == 0), accepted[-1])
                else:
                    time.sleep(0.005)
            elapsed = max(time.monotonic() - point_started, 1e-9)
            thresholds.append(current); counts.append(point_count); rates.append(point_count / elapsed)
            progress_sink(ThresholdScanProgress(current, tuple(thresholds), tuple(counts), tuple(rates), last_event))
    finally:
        try:
            digitizer.stop()
        finally:
            digitizer.close()
    result = ThresholdScanProgress(current, tuple(thresholds), tuple(counts), tuple(rates), last_event, finished=True)
    progress_sink(result)
    LOG.info("Threshold scan finished: points=%d events=%d", len(thresholds), sum(counts))
    return result


def run_threshold_test(
    config: DAQConfig,
    duration_s: float,
    max_events: int,
    stop_event: threading.Event,
    progress_sink: Callable[[ThresholdTestProgress], None],
) -> ThresholdTestProgress:
    if config.trigger["mode"] != "threshold":
        raise ValueError("Threshold test требует trigger.mode: threshold")
    digitizer = CAENDigitizer()
    started = time.monotonic()
    total = 0
    last_event: Event | None = None
    last_update = 0.0
    try:
        board = digitizer.open(config)
        LOG.info("Threshold test connected: %s", board)
        digitizer.reset()
        digitizer.configure(config)
        digitizer.start()
        while not stop_event.is_set() and total < max_events:
            now = time.monotonic()
            if now - started >= duration_s:
                break
            events = digitizer.read_events()
            if events:
                remaining = max_events - total
                event_ids = list(dict.fromkeys(event.event_id for event in events))
                accepted_ids = set(event_ids[:remaining])
                accepted = [event for event in events if event.event_id in accepted_ids]
                total += len(accepted_ids)
                last_event = next((event for event in reversed(accepted) if event.channel == 0), accepted[-1])
            else:
                time.sleep(0.005)
            if now - last_update >= 0.2:
                elapsed = max(now - started, 1e-9)
                progress_sink(ThresholdTestProgress(elapsed, total, total / elapsed, last_event))
                last_update = now
    finally:
        try:
            digitizer.stop()
        finally:
            digitizer.close()
    elapsed = max(time.monotonic() - started, 1e-9)
    result = ThresholdTestProgress(elapsed, total, total / elapsed, last_event)
    progress_sink(result)
    LOG.info("Threshold test finished: events=%d elapsed=%.2fs rate=%.2fHz", total, elapsed, result.event_rate_hz)
    return result

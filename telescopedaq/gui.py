from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .config import load_config
from .event import Event
from .run_control import start_run


class StatisticsBuffer:
    def __init__(self, history_seconds: float = 1800.0, spectrum_limit: int = 100_000) -> None:
        self.history_seconds = history_seconds
        self.times: deque[float] = deque()
        self.amplitudes: deque[float] = deque(maxlen=spectrum_limit)
        self.total = 0

    def add(self, events: list[Event], received_at: float | None = None) -> None:
        now = time.monotonic() if received_at is None else received_at
        self.times.extend([now] * len(events))
        self.amplitudes.extend(event.amplitude for event in events)
        self.total += len(events)
        self.trim(now)

    def trim(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - self.history_seconds
        while self.times and self.times[0] < cutoff:
            self.times.popleft()

    def clear(self) -> None:
        self.times.clear()
        self.amplitudes.clear()
        self.total = 0

    def rate_series(self, interval: float, now: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        current = time.monotonic() if now is None else now
        self.trim(current)
        edges = np.arange(current - self.history_seconds, current + interval, interval)
        counts, edges = np.histogram(np.asarray(self.times), bins=edges)
        centers = (edges[:-1] + edges[1:]) / 2.0
        return (centers - current) / 60.0, counts


class TelescopeDAQGUI:
    def __init__(self, root: tk.Tk, config_path: str | Path) -> None:
        self.root = root
        root.title("TelescopeDAQ Monitor")
        root.geometry("1180x720")
        root.minsize(900, 580)

        self.config_path = tk.StringVar(value=str(Path(config_path)))
        self.refresh_seconds = tk.DoubleVar(value=1.0)
        self.bin_seconds = tk.DoubleVar(value=5.0)
        self.status = tk.StringVar(value="Готов")
        self.board = tk.StringVar(value="АЦП: DT5740D")
        self.run_label = tk.StringVar(value="Run: —")
        self.summary = tk.StringVar(value="Событий: 0    Частота: 0.0 Гц    Время: 0 с")

        self.buffer = StatisticsBuffer()
        self.event_queue: queue.Queue[list[Event]] = queue.Queue()
        self.board_queue: queue.Queue[str] = queue.Queue()
        self.result_queue: queue.Queue[Exception | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.started_at: float | None = None
        self.last_draw = 0.0

        self._build_ui()
        self._load_run_label()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)

    def _build_ui(self) -> None:
        controls = ttk.Frame(self.root, padding=(12, 10))
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="Конфигурация").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(controls, textvariable=self.config_path).grid(row=0, column=1, sticky=tk.EW, padx=(8, 4))
        ttk.Button(controls, text="Обзор...", command=self._browse).grid(row=0, column=2, padx=(0, 12))
        self.start_button = ttk.Button(controls, text="Старт", command=self._start)
        self.start_button.grid(row=0, column=3, padx=4)
        self.stop_button = ttk.Button(controls, text="Стоп", command=self._stop, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=4, padx=4)
        ttk.Button(controls, text="Очистить", command=self._clear).grid(row=0, column=5, padx=(4, 0))
        controls.columnconfigure(1, weight=1)

        settings = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        settings.pack(fill=tk.X)
        ttk.Label(settings, textvariable=self.board).pack(side=tk.LEFT)
        ttk.Separator(settings, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(settings, textvariable=self.run_label).pack(side=tk.LEFT)
        ttk.Separator(settings, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(settings, text="Обновление, с").pack(side=tk.LEFT)
        ttk.Spinbox(settings, from_=0.2, to=60, increment=0.2, width=6, textvariable=self.refresh_seconds).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(settings, text="Интервал, с").pack(side=tk.LEFT)
        ttk.Spinbox(settings, from_=0.5, to=60, increment=0.5, width=6, textvariable=self.bin_seconds).pack(side=tk.LEFT, padx=(6, 14))
        ttk.Label(settings, textvariable=self.status).pack(side=tk.RIGHT)

        self.figure = Figure(figsize=(10, 5.5), dpi=100, layout="constrained")
        self.rate_ax, self.spectrum_ax = self.figure.subplots(1, 2)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.root)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8)

        footer = ttk.Frame(self.root, padding=(12, 8, 12, 12))
        footer.pack(fill=tk.X)
        ttk.Label(footer, textvariable=self.summary).pack(side=tk.LEFT)
        ttk.Label(footer, text="Окно 30 минут · спектр до 100 000 событий").pack(side=tk.RIGHT)
        self._draw()

    def _browse(self) -> None:
        selected = filedialog.askopenfilename(filetypes=(("YAML", "*.yaml *.yml"), ("Все файлы", "*.*")))
        if selected:
            self.config_path.set(selected)
            self._load_run_label()

    def _load_run_label(self) -> None:
        try:
            config = load_config(self.config_path.get())
            self.run_label.set(f"Run: {int(config.run['run_id']):06d}")
        except Exception:
            self.run_label.set("Run: —")

    def _start(self) -> None:
        try:
            config = load_config(self.config_path.get())
        except Exception as error:
            messagebox.showerror("TelescopeDAQ", str(error))
            return
        self.run_label.set(f"Run: {int(config.run['run_id']):06d}")
        self.stop_event.clear()
        self.started_at = time.monotonic()
        self.status.set("Подключение...")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.worker = threading.Thread(target=self._run_acquisition, args=(config.source,), daemon=True)
        self.worker.start()

    def _run_acquisition(self, path: Path) -> None:
        try:
            start_run(path, event_sink=self.event_queue.put, board_sink=self.board_queue.put, stop_event=self.stop_event)
            self.result_queue.put(None)
        except Exception as error:
            self.result_queue.put(error)

    def _run_finished(self, error: Exception | None) -> None:
        self.worker = None
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.status.set("Ошибка" if error else "Остановлен")
        if error:
            messagebox.showerror("Ошибка DAQ", str(error))

    def _stop(self) -> None:
        self.status.set("Остановка...")
        self.stop_button.configure(state=tk.DISABLED)
        self.stop_event.set()

    def _clear(self) -> None:
        self.buffer.clear()
        self.started_at = time.monotonic() if self.worker else None
        self._draw()

    def _poll(self) -> None:
        while True:
            try:
                self.buffer.add(self.event_queue.get_nowait())
            except queue.Empty:
                break
        while True:
            try:
                self.board.set(f"АЦП: {self.board_queue.get_nowait()}")
                self.status.set("Сбор данных")
            except queue.Empty:
                break
        try:
            result = self.result_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self._run_finished(result)
        now = time.monotonic()
        try:
            refresh = max(0.2, float(self.refresh_seconds.get()))
        except (tk.TclError, ValueError):
            refresh = 1.0
        if now - self.last_draw >= refresh:
            self._draw(now)
            self.last_draw = now
        self.root.after(100, self._poll)

    def _draw(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        try:
            interval = max(0.5, float(self.bin_seconds.get()))
        except (tk.TclError, ValueError):
            interval = 5.0
        x, counts = self.buffer.rate_series(interval, current)
        self.rate_ax.clear()
        self.rate_ax.plot(x, counts, color="#2878b5", linewidth=1.6, marker="o", markersize=2.5)
        self.rate_ax.set_xlim(-30, 0)
        self.rate_ax.set(
            title="События во времени",
            xlabel="Время до текущего момента, мин",
            ylabel=f"Количество событий / {interval:g} с",
        )
        self.rate_ax.grid(axis="y", alpha=0.25)

        self.spectrum_ax.clear()
        self.spectrum_ax.hist(list(self.buffer.amplitudes), bins=100, color="#d97706", histtype="stepfilled", alpha=0.8)
        self.spectrum_ax.set(
            title="Спектр максимальной амплитуды",
            xlabel="Максимальная амплитуда события, ADC",
            ylabel="Количество событий",
        )
        self.spectrum_ax.grid(axis="y", alpha=0.25)

        elapsed = current - self.started_at if self.started_at is not None else 0.0
        recent = sum(value >= current - 10.0 for value in self.buffer.times)
        self.summary.set(f"Событий: {self.buffer.total:,}    Частота: {recent / 10.0:.1f} Гц    Время: {elapsed:.0f} с")
        self.canvas.draw_idle()

    def _on_close(self) -> None:
        if self.worker is None:
            self.root.destroy()
            return
        self.stop_event.set()
        self.status.set("Остановка...")
        self.root.after(100, self._wait_and_close)

    def _wait_and_close(self) -> None:
        if self.worker is None:
            self.root.destroy()
        else:
            self.root.after(100, self._wait_and_close)


def launch_gui(config_path: str | Path = "configs/channel0_generator_test.yaml") -> None:
    root = tk.Tk()
    TelescopeDAQGUI(root, config_path)
    root.mainloop()

from __future__ import annotations

import queue
import logging
import shutil
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
import yaml
try:
    import psutil
except ImportError:  # Optional until dependencies are refreshed.
    psutil = None
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .acquisition import AcquisitionStatus
from .caen_digitizer import CAENDigitizer
from .config import DAQConfig, load_config
from .event import Event
from .root_viewer import RootSummary, load_root_summary, load_waveform
from .run_control import start_run
from .threshold_test import ThresholdScanProgress, run_threshold_scan, threshold_values

DAQ_MODES = {"Full Monitor": "full_monitor", "Write Only": "write_only", "ROOT Viewer": "root_viewer"}
TRIGGER_MODES = {"Threshold": "threshold", "External": "external", "Periodic": "periodic"}


class QueueLogHandler(logging.Handler):
    def __init__(self, target: queue.Queue[tuple[str, object]]) -> None:
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        self.target.put(("log", self.format(record)))


def _size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def _elapsed(seconds: float) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _style_axis(axis: object) -> None:
    axis.set_facecolor("#171d1f")
    axis.tick_params(colors="#b9c3c7")
    axis.title.set_color("#e7ecee")
    axis.xaxis.label.set_color("#c8d1d4")
    axis.yaxis.label.set_color("#c8d1d4")
    for spine in axis.spines.values():
        spine.set_color("#465055")


class LiveData:
    def __init__(self) -> None:
        self.rate_times: deque[float] = deque()
        self.rates: deque[float] = deque()
        self.last_event: Event | None = None
        self.last_events: dict[int, Event] = {}
        self.latest_rate = 0.0
        self.channel_counts = [0] * 16

    def add_event(self, event: Event) -> None:
        self.last_event = event
        self.last_events[event.channel] = event
        if 0 <= event.channel < 16: self.channel_counts[event.channel] += 1

    def add_status(self, status: AcquisitionStatus) -> None:
        now = time.monotonic()
        self.latest_rate = status.event_rate_hz
        self.rate_times.append(now)
        self.rates.append(float(status.interval_events))
        while self.rate_times and self.rate_times[0] < now - 60:
            self.rate_times.popleft()
            self.rates.popleft()

    def clear(self) -> None:
        self.__init__()


class TelescopeDAQGUI:
    def __init__(self, root: tk.Tk, config_path: str | Path) -> None:
        self.root = root
        root.title("TelescopeDAQ v0.2 - Data Acquisition System")
        root.geometry("1400x900")
        root.minsize(1020, 680)

        self.config_path = tk.StringVar(value=str(Path(config_path)))
        self.output_path = tk.StringVar(value="output/run_------.root")
        self.daq_mode = tk.StringVar(value="Write Only")
        self.trigger_mode = tk.StringVar(value="Threshold")
        self.caen_state = tk.StringVar(value="DISCONNECTED")
        self.run_label = tk.StringVar(value="Run: ------")
        self.system_state = tk.StringVar(value="System: Ready")
        self.status_vars = {name: tk.StringVar(value=value) for name, value in (
            ("Run ID", "—"), ("Elapsed", "00:00:00"), ("Total events", "0"),
            ("Event rate", "0.0 Hz"), ("Written events", "0"), ("ROOT file size", "0 B"),
            ("Disk free", "—"), ("Last timestamp", "—"), ("CAEN errors", "0"),
        )}
        self.board_vars = {name: tk.StringVar(value=value) for name, value in (
            ("Model", "DT5740D"), ("Connection", "USB"), ("Firmware", "—"),
            ("Serial", "—"), ("ADC", "12 bit"),
        )}

        self.live = LiveData()
        self.event_queue: queue.Queue[list[Event]] = queue.Queue(maxsize=2)
        self.status_queue: queue.Queue[AcquisitionStatus] = queue.Queue(maxsize=4)
        self.board_queue: queue.Queue[str] = queue.Queue()
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.log_handler = QueueLogHandler(self.result_queue)
        self.log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(self.log_handler)
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.viewer_summary: RootSummary | None = None
        self.viewer_request_entry: int | None = None
        self.probe_active = False
        self.threshold_worker: threading.Thread | None = None
        self.threshold_stop = threading.Event()
        self.threshold_window: tk.Toplevel | None = None
        self.threshold_close_pending = False
        self.last_draw = 0.0
        self.last_wave_draw = 0.0
        self.waveform_update_interval_s = 1.0
        self.rate_interval_s = 1.0
        self.waveform_draw_count = 0
        self.last_system_update = 0.0
        self.threshold_adc = 0

        self._style()
        self._build()
        self._load_config_ui()
        self._mode_changed()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)

    def _style(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(bg="#171b1d")
        style.configure(".", background="#202628", foreground="#e7ecee", fieldbackground="#2a3134")
        style.configure("TFrame", background="#171b1d")
        style.configure("Panel.TFrame", background="#202628")
        style.configure("Panel.TLabel", background="#202628", foreground="#e7ecee")
        style.configure("Muted.TLabel", background="#202628", foreground="#aab4b8")
        style.configure("Accent.TButton", background="#15933a", foreground="white")
        style.configure("Danger.TButton", background="#a72d2d", foreground="white")
        style.configure("Invalid.TEntry", fieldbackground="#5a2525", bordercolor="#e45b5b")
        style.configure("Invalid.TCombobox", fieldbackground="#5a2525", bordercolor="#e45b5b")
        style.configure("Error.TLabel", background="#171b1d", foreground="#ff7373")
        style.configure("Valid.TLabel", background="#171b1d", foreground="#55c878")
        style.configure("TNotebook", background="#171b1d", borderwidth=0)
        style.configure("TNotebook.Tab", background="#202628", foreground="#e7ecee", padding=(18, 9))
        style.map("TNotebook.Tab", background=[("selected", "#29363c")], foreground=[("selected", "#55b7ff")])
        style.configure("DAQ.Treeview", background="#171d1f", fieldbackground="#171d1f", foreground="#dce5e8", rowheight=25, borderwidth=0)
        style.configure("DAQ.Treeview.Heading", background="#2a3337", foreground="#f1f5f6", relief="flat")
        style.map("DAQ.Treeview", background=[("selected", "#245a73")], foreground=[("selected", "#ffffff")])
        style.map("DAQ.Treeview.Heading", background=[("active", "#354247")])

    def _build(self) -> None:
        top = ttk.Frame(self.root, style="Panel.TFrame", padding=8)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        fields = (("DAQ Mode", self.daq_mode, tuple(DAQ_MODES), 13), ("Trigger Mode", self.trigger_mode, tuple(TRIGGER_MODES), 12))
        self.mode_boxes: list[ttk.Combobox] = []
        column = 0
        for label, variable, values, width in fields:
            ttk.Label(top, text=label, style="Panel.TLabel").grid(row=0, column=column, padx=(0, 5))
            box = ttk.Combobox(top, textvariable=variable, values=values, state="readonly", width=width)
            self.mode_boxes.append(box)
            box.grid(row=0, column=column + 1, padx=(0, 12))
            if variable is self.daq_mode:
                box.bind("<<ComboboxSelected>>", self._mode_changed)
            column += 2
        ttk.Label(top, text="Config", style="Panel.TLabel").grid(row=0, column=4, padx=(0, 5))
        ttk.Entry(top, textvariable=self.config_path).grid(row=0, column=5, sticky=tk.EW)
        ttk.Button(top, text="...", width=3, command=self._browse_config).grid(row=0, column=6, padx=(4, 12))
        ttk.Label(top, text="Output", style="Panel.TLabel").grid(row=0, column=7, padx=(0, 5))
        ttk.Entry(top, textvariable=self.output_path, state="readonly", width=25).grid(row=0, column=8)
        ttk.Label(top, textvariable=self.caen_state, style="Panel.TLabel").grid(row=0, column=9, padx=12)
        ttk.Label(top, textvariable=self.run_label, style="Panel.TLabel").grid(row=0, column=10)
        top.columnconfigure(5, weight=1)

        actions = ttk.Frame(self.root, style="Panel.TFrame", padding=8)
        actions.pack(fill=tk.X, padx=8, pady=4)
        self.connect_button = ttk.Button(actions, text="Connect CAEN", command=self._connect)
        self.connect_button.pack(side=tk.LEFT, padx=4)
        self.disconnect_button = ttk.Button(actions, text="Disconnect CAEN", command=self._disconnect, state=tk.DISABLED)
        self.disconnect_button.pack(side=tk.LEFT, padx=4)
        self.configure_button = ttk.Button(actions, text="Settings", command=self._configure)
        self.configure_button.pack(side=tk.LEFT, padx=4)
        self.threshold_button = ttk.Button(actions, text="Threshold Test", command=self._open_threshold_test)
        self.threshold_button.pack(side=tk.LEFT, padx=4)
        self.start_button = ttk.Button(actions, text="Start Run", style="Accent.TButton", command=self._start)
        self.start_button.pack(side=tk.LEFT, padx=(20, 4))
        self.stop_button = ttk.Button(actions, text="Stop Run", style="Danger.TButton", command=self._stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=4)
        self.emergency_button = ttk.Button(actions, text="Emergency Stop", style="Danger.TButton", command=self._emergency, state=tk.DISABLED)
        self.emergency_button.pack(side=tk.LEFT, padx=4)
        ttk.Label(actions, textvariable=self.system_state, style="Panel.TLabel").pack(side=tk.RIGHT, padx=6)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))
        self.run_tab, self.wave_tab, self.stats_tab, self.viewer_tab, self.settings_tab, self.logs_tab = [ttk.Frame(self.tabs, padding=8) for _ in range(6)]
        for frame, title in zip((self.run_tab, self.wave_tab, self.stats_tab, self.viewer_tab, self.settings_tab, self.logs_tab), ("Run Control", "Online Waveform", "Run Statistics", "ROOT Viewer", "Settings", "Logs")):
            self.tabs.add(frame, text=title)
        self._run_ui(); self._wave_ui(); self._stats_ui(); self._viewer_ui(); self._settings_ui(); self._logs_ui()
        self.system_info = tk.StringVar(value="CPU: —    Memory: —    Disk: —    Time: —")
        footer = ttk.Frame(self.root, style="Panel.TFrame", padding=(10, 5)); footer.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(footer, text="Supported hardware: CAEN DT5740D · USB · STANDARD firmware", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, textvariable=self.system_info, style="Panel.TLabel").pack(side=tk.RIGHT)

    def _run_ui(self) -> None:
        self.full_dashboard = ttk.Frame(self.run_tab)
        self.full_dashboard.pack(fill=tk.BOTH, expand=True)
        self.dashboard_upper = ttk.Frame(self.full_dashboard); self.dashboard_upper.pack(fill=tk.X, pady=(0, 6))
        upper = self.dashboard_upper
        dash_board = ttk.LabelFrame(upper, text="CAEN DT5740D STATUS", padding=8)
        dash_board.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        for row, (name, variable) in enumerate(self.board_vars.items()):
            ttk.Label(dash_board, text=f"{name}:").grid(row=row, column=0, sticky=tk.W, pady=2)
            ttk.Label(dash_board, textvariable=variable).grid(row=row, column=1, sticky=tk.W, padx=(16, 0), pady=2)
        self.run_info_vars = {name: tk.StringVar(value="—") for name in ("Run ID", "DAQ mode", "Trigger", "Output", "Elapsed", "Events", "Rate")}
        info = ttk.LabelFrame(upper, text="RUN CONTROL INFO", padding=8)
        info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        for index, (name, variable) in enumerate(self.run_info_vars.items()):
            row, col = divmod(index, 2)
            ttk.Label(info, text=f"{name}:").grid(row=row, column=col * 2, sticky=tk.W, pady=2, padx=(0, 6))
            ttk.Label(info, textvariable=variable).grid(row=row, column=col * 2 + 1, sticky=tk.W, pady=2, padx=(0, 14))
        self.dash_rate_fig = Figure(figsize=(4.2, 2.0), dpi=100, layout="constrained", facecolor="#202628")
        self.dash_rate_ax = self.dash_rate_fig.subplots()
        self.dash_rate_canvas = FigureCanvasTkAgg(self.dash_rate_fig, upper)
        self.dashboard_rate_widget = self.dash_rate_canvas.get_tk_widget()
        self.dashboard_rate_widget.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.dashboard_middle = ttk.Frame(self.full_dashboard); self.dashboard_middle.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        middle = self.dashboard_middle
        channels = ttk.LabelFrame(middle, text="CHANNELS OVERVIEW", padding=5)
        channels.pack(fill=tk.BOTH, expand=True)
        columns = ("channel", "enabled", "threshold", "rate", "status")
        self.channel_tree = ttk.Treeview(channels, columns=columns, show="headings", height=9, style="DAQ.Treeview")
        for name, width in (("channel",70),("enabled",90),("threshold",120),("rate",100),("status",110)):
            self.channel_tree.heading(name, text=name.title()); self.channel_tree.column(name, width=width, anchor=tk.CENTER)
        self.channel_tree.pack(fill=tk.BOTH, expand=True)
        for channel in range(16):
            enabled = channel == 0
            self.channel_tree.insert("", tk.END, iid=str(channel), values=(f"{channel:02d}", "Yes" if enabled else "No", "—", "0.0", "OK" if enabled else "Disabled"))

        self.dashboard_lower = ttk.Frame(self.full_dashboard); self.dashboard_lower.pack(fill=tk.X)
        lower = self.dashboard_lower
        cards = ttk.LabelFrame(lower, text="RUN STATISTICS", padding=6); cards.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        for index, name in enumerate(("Total events", "Event rate", "Written events", "ROOT file size", "CAEN errors")):
            cell = ttk.Frame(cards, style="Panel.TFrame", padding=5); cell.grid(row=0, column=index, sticky=tk.NSEW, padx=2)
            ttk.Label(cell, text=name, style="Muted.TLabel").pack(); ttk.Label(cell, textvariable=self.status_vars[name], style="Panel.TLabel").pack()
            cards.columnconfigure(index, weight=1)
        log_panel = ttk.LabelFrame(lower, text="LIVE LOG", padding=4); log_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.dashboard_log = tk.Text(log_panel, height=5, bg="#111719", fg="#c7d2d6", relief=tk.FLAT, font=("Consolas", 9), state=tk.DISABLED)
        self.dashboard_log.pack(fill=tk.BOTH, expand=True)

    def _wave_ui(self) -> None:
        tools = ttk.Frame(self.wave_tab); tools.pack(fill=tk.X, pady=(0, 5))
        self.auto_scale = tk.BooleanVar(value=True); self.show_threshold = tk.BooleanVar(value=True)
        self.wave_channels = [tk.BooleanVar(value=channel == 0) for channel in range(16)]
        ttk.Label(tools, text="Channels").pack(side=tk.LEFT, padx=(0, 5))
        channel_menu = tk.Menu(tools, tearoff=False, bg="#202628", fg="#e7ecee", activebackground="#245a73", activeforeground="white")
        for channel, variable in enumerate(self.wave_channels):
            channel_menu.add_checkbutton(label=f"ch{channel}", variable=variable, command=self._draw_selected_waveforms)
        self.wave_channel_button = ttk.Menubutton(tools, text="Select channels", menu=channel_menu)
        self.wave_channel_button.pack(side=tk.LEFT, padx=(0, 15))
        ttk.Checkbutton(tools, text="Auto scale", variable=self.auto_scale, command=self._draw_selected_waveforms).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(tools, text="Show threshold", variable=self.show_threshold, command=self._draw_selected_waveforms).pack(side=tk.LEFT, padx=5)
        ttk.Button(tools, text="Clear", command=self._clear_live).pack(side=tk.RIGHT)
        self.wave_fig = Figure(figsize=(10, 6), dpi=100, layout="constrained", facecolor="#202628")
        self.wave_ax = self.wave_fig.subplots()
        self.wave_canvas = FigureCanvasTkAgg(self.wave_fig, self.wave_tab)
        self.wave_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.wave_info = tk.StringVar(value="Timestamp: —")
        ttk.Label(self.wave_tab, textvariable=self.wave_info).pack(fill=tk.X, pady=5)
        self.wave_display_status = tk.StringVar(value="Display: latest waveform every 1 s · ROOT: all events")
        ttk.Label(self.wave_tab, textvariable=self.wave_display_status, style="Muted.TLabel").pack(fill=tk.X, pady=(0, 5))
        self._draw_wave(None)

    def _stats_ui(self) -> None:
        self.stats_fig = Figure(figsize=(11, 6), dpi=100, layout="constrained", facecolor="#202628")
        self.rate_ax = self.stats_fig.subplots()
        self.stats_canvas = FigureCanvasTkAgg(self.stats_fig, self.stats_tab)
        self.stats_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._draw_stats()

    def _viewer_ui(self) -> None:
        toolbar = ttk.Frame(self.viewer_tab); toolbar.pack(fill=tk.X, pady=(0, 6))
        self.viewer_path = tk.StringVar(); self.viewer_event = tk.IntVar(); self.viewer_bins = tk.IntVar(value=100); self.viewer_status = tk.StringVar(value="ROOT file not opened")
        ttk.Entry(toolbar, textvariable=self.viewer_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(toolbar, text="Open ROOT", command=self._browse_root).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="<", width=3, command=lambda: self._step_event(-1)).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Label(toolbar, text="Event").pack(side=tk.LEFT, padx=(15, 4))
        self.event_spin = ttk.Spinbox(toolbar, from_=0, to=0, textvariable=self.viewer_event, width=10)
        self.event_spin.pack(side=tk.LEFT)
        ttk.Button(toolbar, text=">", width=3, command=lambda: self._step_event(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Show", command=self._viewer_wave).pack(side=tk.LEFT, padx=5)
        ttk.Label(toolbar, text="Bins").pack(side=tk.LEFT, padx=(10, 3)); ttk.Spinbox(toolbar, from_=10, to=500, increment=10, textvariable=self.viewer_bins, width=6).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Export PNG", command=self._export_viewer).pack(side=tk.LEFT, padx=5)
        ttk.Label(toolbar, textvariable=self.viewer_status).pack(side=tk.RIGHT)
        self.viewer_scale = ttk.Scale(self.viewer_tab, from_=0, to=0, orient=tk.HORIZONTAL, command=self._scale_event)
        self.viewer_scale.pack(fill=tk.X, pady=(0, 5)); self.viewer_scale.bind("<ButtonRelease-1>", lambda _event: self._viewer_wave())
        self.viewer_fig = Figure(figsize=(11, 6), dpi=100, layout="constrained", facecolor="#202628")
        self.vwave, self.vrate = self.viewer_fig.subplots(1, 2)
        self.viewer_canvas = FigureCanvasTkAgg(self.viewer_fig, self.viewer_tab)
        self.viewer_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _logs_ui(self) -> None:
        self.log_text = tk.Text(self.logs_tab, bg="#111719", fg="#c7d2d6", relief=tk.FLAT, font=("Consolas", 10), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self._log("TelescopeDAQ GUI ready")

    def _settings_ui(self) -> None:
        self.setting_vars: dict[tuple[str, str], tk.Variable] = {}
        self.setting_widgets: dict[tuple[str, str], ttk.Widget] = {}
        self.setting_labels: dict[tuple[str, str], str] = {}
        self.setting_specs = (
            ("Run", (("Run ID","run","run_id","int",None),("Run label","run","mode","str",None),("Output directory","run","output_dir","str",None),("Max events","run","max_events","int",None),("File prefix","run","file_prefix","str",None))),
            ("CAEN DT5740D", (("Model","caen","model","readonly",None),("Connection","caen","connection","readonly",None),("Link number","caen","link_num","int",None),("CONET node","caen","conet_node","int",None),("VME base address","caen","vme_base_address","int",None),("Firmware","caen","firmware","readonly",None),("Record length","caen","record_length_samples","int",None),("Pre-trigger, %","caen","pre_trigger_percent","int",None),("Acquisition mode","caen","acquisition_mode","readonly",None),("DC offset","caen","dc_offset","int",None),("Max events BLT","caen","max_events_blt","int",None))),
            ("Channels", (("Number of channels","channels","n_channels","readonly",None),("Enabled channels","channels","enabled","channels",None),("Signal polarity","channels","polarity","choice",("positive","negative")))),
            ("Trigger parameters", (("Threshold channel","threshold","channel","int",None),("Threshold, ADC","threshold","value_adc","int",None),("External input","external","input","readonly",None),("External polarity","external","polarity","choice",("rising","falling")),("Save all enabled","external","save_all_enabled_channels","bool",None),("Periodic interval, s","periodic","interval_s","float",None))),
            ("Storage", (("Write ROOT (required)","storage","write_root","readonly",None),("Save waveforms (required)","storage","save_waveforms","readonly",None),("Compression","storage","compression","choice",("zlib","none")))),
            ("Monitor", (("Enabled","monitor","enabled","bool",None),("Log every events","monitor","update_every_events","int",None),("Waveform display interval, s","monitor","waveform_update_interval_s","float",None),("Rate count interval, s","monitor","rate_interval_s","float",None))),
        )
        grid = ttk.Frame(self.settings_tab); grid.pack(fill=tk.BOTH, expand=True)
        for index, (title, specs) in enumerate(self.setting_specs):
            panel = ttk.LabelFrame(grid, text=title, padding=10); panel.grid(row=index//3, column=index%3, sticky=tk.NSEW, padx=5, pady=5)
            for row, (label, section, key, kind, options) in enumerate(specs):
                ttk.Label(panel, text=label).grid(row=row, column=0, sticky=tk.W, pady=3, padx=(0, 10))
                variable: tk.Variable = tk.BooleanVar() if kind == "bool" else tk.StringVar()
                self.setting_vars[(section, key)] = variable
                self.setting_labels[(section, key)] = label
                if kind == "bool": widget = ttk.Checkbutton(panel, variable=variable)
                elif kind == "choice": widget = ttk.Combobox(panel, textvariable=variable, values=options, state="readonly", width=20)
                else: widget = ttk.Entry(panel, textvariable=variable, width=23, state="readonly" if kind == "readonly" else tk.NORMAL)
                self.setting_widgets[(section, key)] = widget
                widget.grid(row=row, column=1, sticky=tk.EW, pady=3)
                if kind not in {"readonly", "bool"}: variable.trace_add("write", lambda *_args: self._validate_settings_live())
            panel.columnconfigure(1, weight=1)
        for column in range(3): grid.columnconfigure(column, weight=1)
        for row in range(2): grid.rowconfigure(row, weight=1)
        actions = ttk.Frame(self.settings_tab); actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="Reload YAML", command=self._load_settings).pack(side=tk.RIGHT, padx=4)
        self.save_settings_button = ttk.Button(actions, text="Save YAML", style="Accent.TButton", command=self._save_settings)
        self.save_settings_button.pack(side=tk.RIGHT, padx=4)
        ttk.Label(actions, text="CAEN DT5740D · recording ch0..ch15 · threshold trigger ch0").pack(side=tk.LEFT)
        self.settings_error = tk.StringVar(value="")
        self.settings_error_label = ttk.Label(self.settings_tab, textvariable=self.settings_error, style="Error.TLabel")
        self.settings_error_label.pack(fill=tk.X, pady=(5, 0))

    def _log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        for widget in (self.log_text, self.dashboard_log):
            widget.configure(state=tk.NORMAL); widget.insert(tk.END, line)
            if int(widget.index("end-1c").split(".")[0]) > 2000:
                widget.delete("1.0", "201.0")
            widget.see(tk.END); widget.configure(state=tk.DISABLED)

    @staticmethod
    def _offer_latest(target: queue.Queue[object], value: object) -> None:
        try:
            target.put_nowait(value)
        except queue.Full:
            try: target.get_nowait()
            except queue.Empty: pass
            try: target.put_nowait(value)
            except queue.Full: pass

    def _modes(self) -> tuple[str, str]:
        return DAQ_MODES[self.daq_mode.get()], TRIGGER_MODES[self.trigger_mode.get()]

    def _config(self) -> DAQConfig:
        daq, trigger = self._modes()
        return load_config(self.config_path.get(), daq_mode=daq, trigger_mode=trigger)

    def _load_config_ui(self) -> None:
        try: config = load_config(self.config_path.get())
        except Exception as error: self._log(f"Config error: {error}"); return
        self.daq_mode.set({v: k for k, v in DAQ_MODES.items()}[config.daq["mode"]])
        self.trigger_mode.set({v: k for k, v in TRIGGER_MODES.items()}[config.trigger["mode"]])
        run_id = int(config.run["run_id"])
        self.run_label.set(f"Run: {run_id:06d}"); self.status_vars["Run ID"].set(f"{run_id:06d}")
        self.output_path.set(str(Path(config.run["output_dir"]) / f"run_{run_id:06d}.root"))
        self.threshold_adc = int(config.data["threshold"]["value_adc"])
        self.waveform_update_interval_s = float(config.data["monitor"].get("waveform_update_interval_s", 1.0))
        self.rate_interval_s = float(config.data["monitor"].get("rate_interval_s", 1.0))
        if hasattr(self, "wave_display_status"):
            self.wave_display_status.set(f"Display: latest waveform every {self.waveform_update_interval_s:g} s · ROOT: all events")
        if hasattr(self, "channel_tree"):
            enabled_channels = set(config.channels["enabled"])
            for channel in range(16):
                enabled = channel in enabled_channels
                self.channel_tree.set(str(channel), "enabled", "Yes" if enabled else "No")
                self.channel_tree.set(str(channel), "threshold", str(self.threshold_adc) if channel == 0 else "—")
                self.channel_tree.set(str(channel), "status", "OK" if enabled else "Disabled")
        if hasattr(self, "setting_vars"): self._load_settings()

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("YAML", "*.yaml *.yml"), ("All files", "*.*")))
        if path: self.config_path.set(path); self._load_config_ui()

    def _mode_changed(self, _event: object = None) -> None:
        mode = DAQ_MODES[self.daq_mode.get()]
        self.full_dashboard.pack_forget(); self.dashboard_middle.pack_forget(); self.dashboard_rate_widget.pack_forget()
        if mode in {"full_monitor", "write_only"}: self.full_dashboard.pack(fill=tk.BOTH, expand=True)
        if mode == "full_monitor":
            self.dashboard_rate_widget.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
            self.dashboard_middle.pack(fill=tk.BOTH, expand=True, pady=(0, 6), before=self.dashboard_lower)
        target = {"full_monitor": self.run_tab, "write_only": self.run_tab, "root_viewer": self.viewer_tab}[mode]
        self.tabs.select(target)

    def _clear_live(self) -> None:
        self.live.clear(); self.last_wave_draw = 0.0; self.waveform_draw_count = 0
        self._draw_wave(None); self._draw_stats(); self._draw_dashboard()

    def _configure(self) -> None:
        self._load_settings(); self.tabs.select(self.settings_tab)

    def _load_settings(self) -> None:
        try:
            with Path(self.config_path.get()).open("r", encoding="utf-8") as stream: data = yaml.safe_load(stream)
            for _title, specs in self.setting_specs:
                for _label, section, key, kind, _options in specs:
                    value = data[section].get(key, 1.0 if section == "monitor" and key in {"waveform_update_interval_s", "rate_interval_s"} else None)
                    if value is None: raise KeyError(f"{section}.{key}")
                    self.setting_vars[(section, key)].set(",".join(map(str, value)) if kind == "channels" else value)
            self._validate_settings_live()
            self._log(f"Settings loaded: {self.config_path.get()}")
        except Exception as error: messagebox.showerror("Settings", str(error))

    def _settings_errors(self) -> dict[tuple[str, str], str]:
        errors: dict[tuple[str, str], str] = {}
        integer_ranges = {
            ("run", "run_id"): (0, None), ("run", "max_events"): (1, None),
            ("caen", "link_num"): (0, None), ("caen", "conet_node"): (0, None),
            ("caen", "vme_base_address"): (0, None), ("caen", "record_length_samples"): (1, 196608),
            ("caen", "pre_trigger_percent"): (0, 100), ("caen", "dc_offset"): (0, 65535),
            ("caen", "max_events_blt"): (1, None), ("threshold", "channel"): (0, 0),
            ("threshold", "value_adc"): (0, 4095), ("monitor", "update_every_events"): (1, None),
        }
        float_ranges = {
            ("monitor", "waveform_update_interval_s"): (0.05, 60.0),
            ("monitor", "rate_interval_s"): (0.1, 3600.0),
        }
        for _title, specs in self.setting_specs:
            for _label, section, key, kind, options in specs:
                path = (section, key)
                if kind in {"readonly", "bool"}: continue
                raw = str(self.setting_vars[path].get()).strip()
                try:
                    if kind == "int":
                        value = int(raw)
                        minimum, maximum = integer_ranges.get(path, (None, None))
                        if minimum is not None and value < minimum: raise ValueError(f"должно быть >= {minimum}")
                        if maximum is not None and value > maximum: raise ValueError(f"должно быть <= {maximum}")
                    elif kind == "float":
                        value = float(raw)
                        minimum, maximum = float_ranges.get(path, (0.0, None))
                        if value < minimum: raise ValueError(f"должно быть >= {minimum}")
                        if maximum is not None and value > maximum: raise ValueError(f"должно быть <= {maximum}")
                    elif kind == "channels":
                        channels = [int(item.strip()) for item in raw.split(",") if item.strip()]
                        if not channels or len(set(channels)) != len(channels) or any(not 0 <= channel < 16 for channel in channels):
                            raise ValueError("укажите уникальные каналы 0..15")
                    elif kind == "choice" and raw not in (options or ()):
                        raise ValueError("выберите значение из списка")
                    elif kind == "str" and not raw:
                        raise ValueError("не должно быть пустым")
                except ValueError as error:
                    errors[path] = str(error) or "неверное значение"
        return errors

    def _validate_settings_live(self) -> bool:
        errors = self._settings_errors()
        for path, widget in self.setting_widgets.items():
            if isinstance(widget, ttk.Entry): widget.configure(style="Invalid.TEntry" if path in errors else "TEntry")
            elif isinstance(widget, ttk.Combobox): widget.configure(style="Invalid.TCombobox" if path in errors else "TCombobox")
        if errors:
            messages = [f"{self.setting_labels[path]}: {reason}" for path, reason in list(errors.items())[:3]]
            self.settings_error.set(" · ".join(messages)); self.settings_error_label.configure(style="Error.TLabel")
        else:
            self.settings_error.set("Параметры корректны"); self.settings_error_label.configure(style="Valid.TLabel")
        enabled = not errors and self.worker is None and not self.probe_active
        self.save_settings_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        return not errors

    def _guard_settings(self) -> bool:
        if self._validate_settings_live(): return True
        self.tabs.select(self.settings_tab)
        messagebox.showerror("Settings", self.settings_error.get())
        return False

    def _save_settings(self) -> None:
        if self.worker is not None or self.probe_active or not self._validate_settings_live(): return
        source = Path(self.config_path.get()).resolve(); temporary = source.with_suffix(source.suffix + ".tmp")
        try:
            with source.open("r", encoding="utf-8") as stream: data = yaml.safe_load(stream)
            for _title, specs in self.setting_specs:
                for _label, section, key, kind, _options in specs:
                    if kind == "readonly": continue
                    value = self.setting_vars[(section, key)].get()
                    if kind == "int": value = int(value)
                    elif kind == "float": value = float(value)
                    elif kind == "channels": value = [int(item.strip()) for item in str(value).split(",") if item.strip()]
                    elif kind == "bool": value = bool(value)
                    data[section][key] = value
            with temporary.open("w", encoding="utf-8") as stream: yaml.safe_dump(data, stream, sort_keys=False, allow_unicode=True)
            load_config(temporary)
            temporary.replace(source)
            selected_daq, selected_trigger = self.daq_mode.get(), self.trigger_mode.get()
            self._load_config_ui(); self._mode_changed(); self.system_state.set("System: Configured")
            self.daq_mode.set(selected_daq); self.trigger_mode.set(selected_trigger); self._mode_changed()
            self._log(f"Settings saved and validated: {source}")
        except Exception as error:
            if temporary.exists(): temporary.unlink()
            messagebox.showerror("Settings", str(error))

    def _open_threshold_test(self) -> None:
        if self.worker is not None or self.probe_active: return
        if self.threshold_window is not None and self.threshold_window.winfo_exists():
            self.threshold_window.deiconify(); self.threshold_window.lift(); return
        window = tk.Toplevel(self.root); self.threshold_window = window
        window.title("CAEN DT5740D · Threshold Test"); window.geometry("900x620"); window.minsize(720, 520)
        window.protocol("WM_DELETE_WINDOW", self._close_threshold_test)
        controls = ttk.Frame(window, padding=10); controls.pack(fill=tk.X)
        self.test_channel = tk.StringVar(value="0")
        configured = int(self.setting_vars[("threshold", "value_adc")].get())
        self.test_lower = tk.StringVar(value=str(max(0, configured - 200)))
        self.test_upper = tk.StringVar(value=str(min(4095, configured + 200)))
        self.test_step = tk.StringVar(value="20")
        self.test_dwell = tk.StringVar(value="2")
        self.test_max_events = tk.StringVar(value="10000")
        self.test_selected_threshold = tk.StringVar(value=str(configured))
        scan_fields = (("Channel",self.test_channel,("0",)),("Lower, ADC",self.test_lower,None),("Upper, ADC",self.test_upper,None),("Step, ADC",self.test_step,None),("Time / point, s",self.test_dwell,None),("Max events / point",self.test_max_events,None))
        for column, (label, variable, values) in enumerate(scan_fields):
            block = ttk.Frame(controls); block.grid(row=0, column=column, padx=(0, 12), sticky=tk.W)
            ttk.Label(block, text=label).pack(anchor=tk.W)
            if values: ttk.Combobox(block, textvariable=variable, values=values, state="readonly", width=12).pack()
            else: ttk.Entry(block, textvariable=variable, width=14).pack()
        self.test_start_button = ttk.Button(controls, text="Start Test", style="Accent.TButton", command=self._start_threshold_test)
        self.test_start_button.grid(row=1, column=0, padx=4, pady=(10,0), sticky=tk.W)
        self.test_stop_button = ttk.Button(controls, text="Stop", style="Danger.TButton", state=tk.DISABLED, command=self.threshold_stop.set)
        self.test_stop_button.grid(row=1, column=1, padx=4, pady=(10,0), sticky=tk.W)
        ttk.Label(controls, text="Selected threshold").grid(row=1, column=3, sticky=tk.E, pady=(10,0))
        ttk.Entry(controls, textvariable=self.test_selected_threshold, width=10).grid(row=1, column=4, sticky=tk.W, pady=(10,0))
        ttk.Button(controls, text="Use in Settings", command=self._use_test_threshold).grid(row=1, column=5, padx=4, pady=(10,0), sticky=tk.W)
        self.test_status = tk.StringVar(value="Ready · scan does not write ROOT")
        self.test_stats = tk.StringVar(value="Points: 0    Events: 0    Current threshold: —")
        ttk.Label(window, textvariable=self.test_status, padding=(10, 2)).pack(fill=tk.X)
        ttk.Label(window, textvariable=self.test_stats, padding=(10, 2), font=("Segoe UI", 11, "bold")).pack(fill=tk.X)
        self.test_fig = Figure(figsize=(8, 4.5), dpi=100, layout="constrained", facecolor="#202628")
        self.threshold_scan_progress: ThresholdScanProgress | None = None
        self.test_scan_ax, self.test_wave_ax = self.test_fig.subplots(1, 2); self.test_canvas = FigureCanvasTkAgg(self.test_fig, window)
        self.test_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self._draw_threshold_progress(None)
        self.test_fig.canvas.mpl_connect("button_press_event", self._select_threshold_point)

    def _start_threshold_test(self) -> None:
        if self.threshold_worker is not None or self.worker is not None or self.probe_active: return
        try:
            channel = int(self.test_channel.get()); lower = int(self.test_lower.get()); upper = int(self.test_upper.get())
            step = int(self.test_step.get()); dwell = float(self.test_dwell.get()); max_events = int(self.test_max_events.get())
            if channel != 0: raise ValueError("TelescopeDAQ v0.1 поддерживает threshold test только канала 0")
            values = threshold_values(lower, upper, step)
            if dwell <= 0 or max_events <= 0: raise ValueError("Time per point и Max events должны быть больше нуля")
            config = load_config(self.config_path.get(), daq_mode="full_monitor", trigger_mode="threshold")
            config.data["threshold"].update(channel=channel, value_adc=values[0], enabled=True)
        except Exception as error:
            messagebox.showerror("Threshold Test", str(error), parent=self.threshold_window); return
        self.threshold_stop.clear(); self.threshold_close_pending = False
        self.test_status.set("Connecting and configuring DT5740D...")
        self.test_start_button.configure(state=tk.DISABLED); self.test_stop_button.configure(state=tk.NORMAL)
        self.start_button.configure(state=tk.DISABLED); self.connect_button.configure(state=tk.DISABLED); self.configure_button.configure(state=tk.DISABLED); self.threshold_button.configure(state=tk.DISABLED)
        for box in self.mode_boxes: box.configure(state=tk.DISABLED)
        self.threshold_worker = threading.Thread(target=self._threshold_test_worker, args=(config, lower, upper, step, dwell, max_events), daemon=True)
        self.threshold_worker.start(); self._log(f"Threshold scan started: ch={channel}, range={lower}..{upper}, step={step} ADC")

    def _threshold_test_worker(self, config: DAQConfig, lower: int, upper: int, step: int, dwell: float, max_events: int) -> None:
        try:
            result = run_threshold_scan(config, lower, upper, step, dwell, max_events, self.threshold_stop, lambda progress: self.result_queue.put(("threshold_progress", progress)))
            self.result_queue.put(("threshold_done", result))
        except Exception as error: self.result_queue.put(("threshold_error", error))

    def _draw_threshold_progress(self, progress: ThresholdScanProgress | None) -> None:
        self.threshold_scan_progress = progress
        self.test_scan_ax.clear(); self.test_wave_ax.clear()
        self.test_scan_ax.set(title="Threshold scan", xlabel="Threshold, ADC", ylabel="Trigger count"); self.test_scan_ax.grid(alpha=.2)
        self.test_wave_ax.set(title="Last waveform · ch0", xlabel="Samples", ylabel="ADC"); self.test_wave_ax.grid(alpha=.2)
        if progress is not None:
            self.test_scan_ax.plot(progress.thresholds, progress.counts, color="#32b643", marker="o", linewidth=1.4)
            total = sum(progress.counts)
            self.test_stats.set(f"Points: {len(progress.thresholds)}    Events: {total:,}    Current threshold: {progress.current_threshold} ADC")
            if progress.last_event is not None:
                event = progress.last_event; self.test_wave_ax.plot(event.waveform, color="#2396e8", linewidth=1)
                self.test_wave_ax.axhline(progress.current_threshold, color="#e4b526", linestyle="--", label=f"Threshold {progress.current_threshold}")
                self.test_wave_ax.legend(loc="upper right", fontsize=8)
        try: selected = int(self.test_selected_threshold.get()); self.test_scan_ax.axvline(selected, color="#e4b526", linestyle="--", alpha=.8)
        except ValueError: pass
        _style_axis(self.test_scan_ax); _style_axis(self.test_wave_ax); self.test_canvas.draw_idle()

    def _select_threshold_point(self, event: object) -> None:
        if event.inaxes is not self.test_scan_ax or event.xdata is None: return
        selected = int(round(event.xdata)); self.test_selected_threshold.set(str(min(max(selected, 0), 4095)))
        self._draw_threshold_progress(self.threshold_scan_progress)

    def _finish_threshold_test(self, error: object | None = None) -> None:
        self.threshold_worker = None; self.threshold_stop.clear()
        if self.threshold_window is not None and self.threshold_window.winfo_exists():
            self.test_start_button.configure(state=tk.NORMAL); self.test_stop_button.configure(state=tk.DISABLED)
            self.test_status.set("Error" if error else "Finished · ROOT was not written")
        self.start_button.configure(state=tk.NORMAL); self.connect_button.configure(state=tk.NORMAL); self.configure_button.configure(state=tk.NORMAL); self.threshold_button.configure(state=tk.NORMAL)
        for box in self.mode_boxes: box.configure(state="readonly")
        if error is not None: self._log(f"Threshold test error: {error}"); messagebox.showerror("Threshold Test", str(error), parent=self.threshold_window)
        else: self._log("Threshold test finished")
        if self.threshold_close_pending and self.threshold_window is not None:
            self.threshold_window.destroy(); self.threshold_window = None; self.threshold_close_pending = False

    def _use_test_threshold(self) -> None:
        try:
            value = int(self.test_selected_threshold.get())
            if not 0 <= value <= 4095: raise ValueError
        except ValueError:
            messagebox.showerror("Threshold Test", "Threshold должен быть в диапазоне 0..4095 ADC", parent=self.threshold_window); return
        self.setting_vars[("threshold", "value_adc")].set(str(value))
        self.trigger_mode.set("Threshold"); self._validate_settings_live(); self.tabs.select(self.settings_tab)
        self._log(f"Threshold {value} ADC copied to Settings; YAML is not saved yet")

    def _close_threshold_test(self) -> None:
        if self.threshold_worker is not None:
            self.threshold_close_pending = True; self.threshold_stop.set(); self.test_status.set("Stopping..."); return
        if self.threshold_window is not None: self.threshold_window.destroy(); self.threshold_window = None

    def _connect(self) -> None:
        if self.worker is not None or self.threshold_worker is not None or self.probe_active or not self._guard_settings(): return
        try: config = self._config()
        except Exception as error: messagebox.showerror("CAEN", str(error)); return
        self.probe_active = True; self.connect_button.configure(state=tk.DISABLED)
        self.disconnect_button.configure(state=tk.DISABLED)
        self.save_settings_button.configure(state=tk.DISABLED)
        self.caen_state.set("CONNECTING"); threading.Thread(target=self._probe, args=(config,), daemon=True).start()

    def _probe(self, config: DAQConfig) -> None:
        digitizer = CAENDigitizer()
        try: self.result_queue.put(("connected", digitizer.open(config)))
        except Exception as error: self.result_queue.put(("connect_error", error))
        finally: digitizer.close()

    def _disconnect(self) -> None:
        if self.worker is not None:
            messagebox.showinfo("CAEN", "Сначала остановите run кнопкой Stop Run.")
            return
        if self.probe_active:
            messagebox.showinfo("CAEN", "Дождитесь завершения проверки подключения.")
            return
        self.caen_state.set("DISCONNECTED"); self.system_state.set("System: Ready")
        self.disconnect_button.configure(state=tk.DISABLED)
        self._log("CAEN disconnected")

    def _start(self) -> None:
        if not self._guard_settings(): return
        if self.threshold_worker is not None:
            messagebox.showinfo("Threshold Test", "Сначала остановите threshold test.")
            return
        if self.probe_active:
            messagebox.showinfo("CAEN", "Дождитесь завершения проверки подключения.")
            return
        daq, trigger = self._modes()
        if daq == "root_viewer": self.tabs.select(self.viewer_tab); self._browse_root(); return
        try: config = self._config()
        except Exception as error: messagebox.showerror("TelescopeDAQ", str(error)); return
        self.live.clear(); self.last_wave_draw = 0.0; self.waveform_draw_count = 0
        self.stop_event.clear(); self.caen_state.set("CONNECTING"); self.system_state.set("System: Starting")
        self.start_button.configure(state=tk.DISABLED); self.stop_button.configure(state=tk.NORMAL); self.emergency_button.configure(state=tk.NORMAL)
        self.connect_button.configure(state=tk.DISABLED); self.configure_button.configure(state=tk.DISABLED)
        self.disconnect_button.configure(state=tk.DISABLED)
        self.threshold_button.configure(state=tk.DISABLED)
        self.save_settings_button.configure(state=tk.DISABLED)
        for box in self.mode_boxes: box.configure(state=tk.DISABLED)
        self.worker = threading.Thread(target=self._run, args=(config.source, daq, trigger), daemon=True); self.worker.start()
        self._log(f"Starting run {config.run['run_id']}: {daq}, {trigger}"); self._mode_changed()

    def _run(self, path: Path, daq: str, trigger: str) -> None:
        try:
            result = start_run(
                path,
                event_sink=lambda value: self._offer_latest(self.event_queue, value),
                board_sink=self.board_queue.put,
                status_sink=lambda value: self._offer_latest(self.status_queue, value),
                stop_event=self.stop_event,
                daq_mode=daq,
                trigger_mode=trigger,
            )
            self.result_queue.put(("run_done", result))
        except Exception as error: self.result_queue.put(("run_error", error))

    def _stop(self) -> None:
        self.system_state.set("System: Stopping"); self.stop_button.configure(state=tk.DISABLED); self.stop_event.set(); self._log("Stop requested")

    def _emergency(self) -> None:
        self.system_state.set("System: EMERGENCY STOP"); self.stop_event.set(); self.stop_button.configure(state=tk.DISABLED); self.emergency_button.configure(state=tk.DISABLED); self._log("EMERGENCY STOP requested")

    def _apply_status(self, status: AcquisitionStatus) -> None:
        self.live.add_status(status)
        interval_label = f"{status.rate_interval_s:g} s"
        values = (f"{status.run_id:06d}", _elapsed(status.elapsed_s), f"{status.total_events:,}", f"{status.interval_events:,} / {interval_label}", f"{status.written_events:,}", _size(status.root_size_bytes), _size(status.disk_free_bytes), "—" if status.last_timestamp is None else str(status.last_timestamp), str(status.caen_errors))
        for variable, value in zip(self.status_vars.values(), values): variable.set(value)
        self.system_state.set(f"System: {status.state.title()}")
        self.run_info_vars["Run ID"].set(f"{status.run_id:06d}")
        self.run_info_vars["DAQ mode"].set(self.daq_mode.get())
        self.run_info_vars["Trigger"].set(self.trigger_mode.get())
        self.run_info_vars["Output"].set(Path(self.output_path.get()).name)
        self.run_info_vars["Elapsed"].set(_elapsed(status.elapsed_s))
        self.run_info_vars["Events"].set(f"{status.total_events:,}")
        self.run_info_vars["Rate"].set(f"{status.interval_events:,} events / {interval_label}")

    def _poll(self) -> None:
        while True:
            try: batch = self.event_queue.get_nowait()
            except queue.Empty: break
            for event in batch: self.live.add_event(event)
        while True:
            try: self._apply_status(self.status_queue.get_nowait())
            except queue.Empty: break
        while True:
            try: board = self.board_queue.get_nowait()
            except queue.Empty: break
            self.caen_state.set("CONNECTED"); self.board_vars["Model"].set(board.split(" · ", 1)[0]); self._log(f"CAEN connected: {board}")
        while True:
            try: kind, payload = self.result_queue.get_nowait()
            except queue.Empty: break
            self._result(kind, payload)
        now = time.monotonic()
        if self._modes()[0] == "full_monitor":
            active_tab = self.tabs.select()
            if active_tab == str(self.wave_tab) and now - self.last_wave_draw >= self.waveform_update_interval_s:
                self._draw_wave(self.live.last_event); self.last_wave_draw = now
            elif active_tab != str(self.wave_tab) and now - self.last_draw >= 1:
                if active_tab == str(self.stats_tab): self._draw_stats()
                elif active_tab == str(self.run_tab): self._draw_dashboard()
                self.last_draw = now
        if time.monotonic() - self.last_system_update >= 1:
            disk = shutil.disk_usage(Path.cwd()); disk_used = 100 * (disk.total - disk.free) / disk.total
            cpu = f"{psutil.cpu_percent():.0f}%" if psutil is not None else "—"
            memory = f"{psutil.virtual_memory().percent:.0f}%" if psutil is not None else "—"
            self.system_info.set(f"CPU: {cpu}    Memory: {memory}    Disk: {disk_used:.0f}%    Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.last_system_update = time.monotonic()
        self.root.after(100, self._poll)

    def _result(self, kind: str, payload: object) -> None:
        if kind == "connected":
            self.probe_active = False; self.connect_button.configure(state=tk.NORMAL)
            self.disconnect_button.configure(state=tk.NORMAL)
            self._validate_settings_live()
            board = payload; assert isinstance(board, dict)
            self.caen_state.set("AVAILABLE"); self.system_state.set("System: CAEN ready")
            for key, value in (("Model", board["model"]), ("Serial", board["serial_number"]), ("ADC", f"{board['adc_bits']} bit"), ("Firmware", board["roc_firmware"])): self.board_vars[key].set(str(value))
            self._log(f"CAEN available: {board['model']} S/N {board['serial_number']}")
        elif kind in ("connect_error", "run_error"):
            if kind == "connect_error": self.probe_active = False; self.connect_button.configure(state=tk.NORMAL); self.disconnect_button.configure(state=tk.DISABLED); self._validate_settings_live()
            self.caen_state.set("ERROR"); self.system_state.set("System: Error"); self._log(f"{kind}: {payload}"); messagebox.showerror("TelescopeDAQ", str(payload))
            if kind == "run_error": self._run_finished()
        elif kind == "root_error":
            self.viewer_status.set("ROOT error"); self._log(f"ROOT Viewer: {payload}"); messagebox.showerror("ROOT Viewer", str(payload))
        elif kind == "run_done": self._log(f"Run finished: {payload}"); self.caen_state.set("DISCONNECTED"); self._run_finished()
        elif kind == "root_loaded":
            summary, waveform, entry = payload; self._draw_root(summary, waveform, entry); self._log(f"ROOT opened: {summary.path}")
        elif kind == "wave_loaded":
            waveform, entry = payload
            if self.viewer_summary is not None and entry == self.viewer_request_entry: self._draw_root(self.viewer_summary, waveform, entry)
        elif kind == "threshold_progress":
            if self.threshold_window is not None and self.threshold_window.winfo_exists():
                assert isinstance(payload, ThresholdScanProgress); self.test_status.set("Scanning threshold..."); self._draw_threshold_progress(payload)
        elif kind == "threshold_done":
            if self.threshold_window is not None and self.threshold_window.winfo_exists(): self._draw_threshold_progress(payload)
            self._finish_threshold_test()
        elif kind == "threshold_error": self._finish_threshold_test(payload)
        elif kind == "log": self._log(str(payload))

    def _run_finished(self) -> None:
        self.worker = None; self.start_button.configure(state=tk.NORMAL); self.stop_button.configure(state=tk.DISABLED); self.emergency_button.configure(state=tk.DISABLED)
        self.connect_button.configure(state=tk.NORMAL); self.configure_button.configure(state=tk.NORMAL)
        self.disconnect_button.configure(state=tk.DISABLED)
        self.threshold_button.configure(state=tk.NORMAL)
        self._validate_settings_live()
        for box in self.mode_boxes: box.configure(state="readonly")

    def _draw_wave(self, event: Event | None) -> None:
        if event is not None:
            self.live.last_events[event.channel] = event
        self._draw_selected_waveforms()

    def _draw_selected_waveforms(self) -> None:
        selected = [channel for channel, variable in enumerate(self.wave_channels) if variable.get()]
        self.wave_channel_button.configure(text="ch" + ", ch".join(map(str, selected)) if selected else "No channels")
        self.wave_ax.clear(); self.wave_ax.set(title="Online Waveform", xlabel="Samples", ylabel="ADC"); self.wave_ax.grid(alpha=.2)
        colors = ("#2396e8", "#32b643", "#e4b526", "#d66b5d", "#a77be8", "#42c6c7")
        shown: list[Event] = []
        for index, channel in enumerate(selected):
            event = self.live.last_events.get(channel)
            if event is None: continue
            shown.append(event); self.wave_ax.plot(event.waveform, color=colors[index % len(colors)], linewidth=1, label=f"ch{channel}")
        if self.show_threshold.get() and 0 in selected: self.wave_ax.axhline(self.threshold_adc, color="#e4b526", linestyle=":", label=f"Threshold {self.threshold_adc}")
        if not self.auto_scale.get(): self.wave_ax.set_ylim(0, 4095)
        if shown: self.wave_ax.legend(loc="upper right", fontsize=8)
        if shown:
            self.waveform_draw_count += 1
            self.wave_display_status.set(f"Display updates: {self.waveform_draw_count:,} · latest every {self.waveform_update_interval_s:g} s · ROOT: all events")
        if len(shown) == 1:
            event = shown[0]; self.wave_info.set(f"ch{event.channel}    Samples: {len(event.waveform)}    Timestamp: {event.timestamp}")
        elif shown: self.wave_info.set(f"Displayed channels: {', '.join(f'ch{event.channel}' for event in shown)}")
        else: self.wave_info.set("No waveform available for selected channels")
        _style_axis(self.wave_ax)
        self.wave_canvas.draw_idle()

    def _draw_dashboard(self) -> None:
        self.dash_rate_ax.clear(); self.dash_rate_ax.grid(axis="y", alpha=.2)
        if self.live.rate_times:
            now = time.monotonic()
            self.dash_rate_ax.plot([value-now for value in self.live.rate_times], self.live.rates, color="#32b643")
        self.dash_rate_ax.set(title="Accepted events per interval", xlabel="Seconds to now", ylabel=f"Events / {self.rate_interval_s:g} s", xlim=(-60, 0)); _style_axis(self.dash_rate_ax)
        self.dash_rate_canvas.draw_idle()
        for channel in range(16):
            count = self.live.channel_counts[channel]
            if count: self.channel_tree.set(str(channel), "rate", f"{self.live.latest_rate:.1f}")

    def _draw_stats(self) -> None:
        self.rate_ax.clear(); self.rate_ax.grid(axis="y", alpha=.2)
        if self.live.rate_times:
            now = time.monotonic(); self.rate_ax.plot([(v-now)/60 for v in self.live.rate_times], self.live.rates, color="#32b643")
        self.rate_ax.set(title="Accepted events per interval", xlabel="Minutes to now", ylabel=f"Events / {self.rate_interval_s:g} s", xlim=(-30, 0))
        _style_axis(self.rate_ax)
        self.stats_canvas.draw_idle()

    def _browse_root(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("ROOT files", "*.root"), ("All files", "*.*")))
        if not path: return
        if self.worker is not None and Path(path).resolve() == Path(self.output_path.get()).resolve():
            messagebox.showwarning("ROOT Viewer", "Активный ROOT текущего run можно открыть после остановки DAQ.")
            return
        self.viewer_path.set(path); self.viewer_status.set("Loading..."); threading.Thread(target=self._root_worker, args=(Path(path),), daemon=True).start()

    def _root_worker(self, path: Path) -> None:
        try:
            summary = load_root_summary(path); waveform = load_waveform(path, 0) if summary.count else np.asarray([], dtype=np.uint16)
            self.result_queue.put(("root_loaded", (summary, waveform, 0)))
        except Exception as error: self.result_queue.put(("root_error", error))

    def _viewer_wave(self) -> None:
        if self.viewer_summary is None: return
        self.viewer_request_entry = self.viewer_event.get()
        threading.Thread(target=self._wave_worker, args=(self.viewer_summary.path, self.viewer_request_entry), daemon=True).start()

    def _scale_event(self, value: str) -> None:
        self.viewer_event.set(int(float(value)))

    def _step_event(self, step: int) -> None:
        if self.viewer_summary is None: return
        self.viewer_event.set(min(max(self.viewer_event.get() + step, 0), self.viewer_summary.count - 1)); self.viewer_scale.set(self.viewer_event.get()); self._viewer_wave()

    def _export_viewer(self) -> None:
        if self.viewer_summary is None: return
        target = filedialog.asksaveasfilename(defaultextension=".png", filetypes=(("PNG", "*.png"),))
        if target: self.viewer_fig.savefig(target, dpi=150, facecolor=self.viewer_fig.get_facecolor()); self._log(f"ROOT viewer exported: {target}")

    def _wave_worker(self, path: Path, entry: int) -> None:
        try: self.result_queue.put(("wave_loaded", (load_waveform(path, entry), entry)))
        except Exception as error: self.result_queue.put(("root_error", error))

    def _draw_root(self, summary: RootSummary, waveform: np.ndarray, entry: int) -> None:
        self.viewer_summary = summary; self.event_spin.configure(to=max(0, summary.count-1)); self.viewer_scale.configure(to=max(0, summary.count-1)); self.viewer_event.set(entry); self.viewer_scale.set(entry)
        for axis in (self.vwave, self.vrate): axis.clear(); axis.grid(axis="y", alpha=.2)
        self.vwave.plot(waveform, color="#2396e8"); self.vwave.set(title=f"Waveform · event {entry}", xlabel="Samples", ylabel="ADC")
        bins = min(max(self.viewer_bins.get(), 10), 500)
        x, counts = summary.rate_series(bins); self.vrate.plot(x, counts, color="#32b643"); self.vrate.set(title="Rate vs time", xlabel="Relative timestamp, ticks", ylabel="Events / bin")
        for axis in (self.vwave, self.vrate): _style_axis(axis)
        metadata = ""
        if summary.count: metadata = f" · id={summary.event_ids[entry]} · ts={summary.timestamps[entry]}"
        self.viewer_status.set(f"{summary.count:,} events{metadata}"); self.viewer_canvas.draw_idle()

    def _close(self) -> None:
        if self.worker is None and self.threshold_worker is None: logging.getLogger().removeHandler(self.log_handler); self.root.destroy(); return
        self.stop_event.set(); self.threshold_stop.set(); self.system_state.set("System: Stopping"); self.root.after(100, self._wait_close)

    def _wait_close(self) -> None:
        if self.worker is None and self.threshold_worker is None: logging.getLogger().removeHandler(self.log_handler); self.root.destroy()
        else: self.root.after(100, self._wait_close)


def launch_gui(config_path: str | Path = "configs/channel0_generator_test.yaml") -> None:
    root = tk.Tk(); TelescopeDAQGUI(root, config_path); root.mainloop()

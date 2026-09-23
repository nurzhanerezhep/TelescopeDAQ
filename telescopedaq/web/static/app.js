"use strict";
const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n || 0).toLocaleString("en-US");
const colors = DAQCharts.palette;
let view = "run",
  config = null,
  revision = "",
  dirty = false,
  snapshot = {},
  waveSequence = -1;
let waves = [],
  lastWaveFetch = 0,
  paused = false,
  logId = 0,
  logRows = [],
  rootPath = "",
  rootSummary = null,
  pending = false;
let exiting = false,
  exitReady = false;
let lastError = "",
  validationTimer = null,
  validationSequence = 0,
  settingsValid = true;
const selected = new Set([0]);
const titles = {
  run: "Run Control",
  wave: "Online Waveform",
  stats: "Run Statistics",
  viewer: "ROOT Viewer",
  settings: "Settings",
  logs: "Logs",
};

function icons() {
  lucide.createIcons();
}

function bytes(n) {
  let i = 0;
  while (n >= 1024 && i < 4) {
    n /= 1024;
    i++;
  }
  return `${Number(n || 0).toFixed(i ? 1 : 0)} ${["B", "KiB", "MiB", "GiB", "TiB"][i]}`;
}

function elapsed(n) {
  n = Math.floor(n || 0);
  return [Math.floor(n / 3600), Math.floor(n / 60) % 60, n % 60]
    .map((x) => String(x).padStart(2, "0"))
    .join(":");
}

function error(message) {
  $("error").hidden = false;
  $("error").querySelector("span").textContent = message;
}
async function api(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method,
    headers: body
      ? {
          "Content-Type": "application/json",
        }
      : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    let message = data.detail || "Request failed";
    if (Array.isArray(message))
      message = message.map((x) => `${x.loc.join(".")}: ${x.msg}`).join("; ");
    throw new Error(message);
  }
  return data;
}
async function command(path, body) {
  if (pending) return;
  pending = true;
  updateButtons();
  try {
    await api(path, "POST", body);
    $("error").hidden = true;
    await pollState();
  } catch (e) {
    error(e.message);
  } finally {
    pending = false;
    updateButtons();
  }
}

function download(name, content, type = "text/plain") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(
    new Blob([content], {
      type,
    }),
  );
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function setView(next) {
  view = next;
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== "view-" + next));
  document
    .querySelectorAll(".nav-button")
    .forEach((el) => el.classList.toggle("active", el.dataset.view === next));
  $("view-title").textContent = titles[next];
  renderCharts();
  if (next === "wave") renderWaves();
  if (next === "viewer") refreshFiles();
  if (next === "logs") renderLogs();
}
document
  .querySelectorAll("[data-view]")
  .forEach((el) =>
    el.addEventListener("click", () => setView(el.dataset.view)),
  );
$("dismiss-error").onclick = () => {
  $("error").hidden = true;
};
$("connect").onclick = () => command("/connect");
$("disconnect").onclick = () => command("/disconnect");
$("start").onclick = () =>
  command("/run/start", {
    daq_mode: $("daq-mode").value,
    trigger_mode: $("trigger-mode").value,
  });
$("stop").onclick = () => command("/run/stop");
$("emergency").onclick = () => command("/run/emergency");
$("safe-exit").onclick = () => {
  $("exit-status").textContent = dirty
    ? "Unsaved Settings will be discarded. Stop the run, write buffered ROOT data and exit?"
    : "The active run will stop. Buffered ROOT data will be written and CAEN disconnected.";
  $("exit-dialog").showModal();
};
$("cancel-exit").onclick = () => $("exit-dialog").close();
$("exit-dialog").addEventListener("cancel", (event) => {
  if (exiting) event.preventDefault();
});
$("confirm-exit").onclick = async () => {
  exiting = true;
  $("confirm-exit").disabled = true;
  $("cancel-exit").disabled = true;
  $("exit-progress").hidden = false;
  $("exit-status").textContent =
    "Stopping acquisition and closing ROOT. Waiting for CAEN cleanup...";
  updateButtons();
  try {
    await api("/shutdown", "POST", { confirmation: "stop_and_exit" });
    await pollState();
  } catch (e) {
    $("exit-status").textContent = "Exit not confirmed: " + e.message;
    exiting = false;
    $("confirm-exit").disabled = false;
    $("exit-progress").hidden = true;
    $("cancel-exit").disabled = false;
    updateButtons();
  }
};
window.addEventListener("beforeunload", (event) => {
  if (!exitReady && (snapshot.busy || dirty)) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("daq-mode").onchange = () => {
  if ($("daq-mode").value === "root_viewer") setView("viewer");
  updateButtons();
  renderState();
};
$("trigger-mode").onchange = () => updateButtons();

function updateButtons() {
  const busy =
    snapshot.busy ||
    pending ||
    snapshot.closed ||
    exiting ||
    snapshot.read_only;
  $("safe-exit").disabled = !!(
    exiting ||
    snapshot.closed ||
    snapshot.read_only
  );
  for (const id of ["yaml-upload", "root-upload", "use-threshold"])
    $(id).disabled = !!(snapshot.read_only || snapshot.closed);
  for (const id of [
    "connect",
    "disconnect",
    "open-scan",
    "daq-mode",
    "trigger-mode",
  ])
    $(id).disabled = !!busy;
  $("disconnect").disabled = busy || !snapshot.connected;
  $("start").disabled =
    busy || dirty || !settingsValid || $("daq-mode").value === "root_viewer";
  const stopping = snapshot.state === "stopping";
  const canStop =
    snapshot.busy &&
    ["acquiring", "scanning"].includes(snapshot.operation) &&
    !stopping &&
    !snapshot.closed &&
    !exiting &&
    !snapshot.read_only;
  $("stop").disabled = !canStop;
  $("emergency").disabled = !canStop;
  $("scan-stop").disabled =
    snapshot.operation !== "scanning" ||
    stopping ||
    snapshot.closed ||
    snapshot.read_only;
  $("scan-start").disabled = busy || dirty;
  $("save-settings").disabled = busy || !dirty || !settingsValid;
  $("settings-form")
    .querySelectorAll("input,select")
    .forEach((el) => (el.disabled = !!busy));
}

function renderState() {
  $("access-mode").hidden = !snapshot.read_only;
  if (snapshot.read_only && snapshot.run_config) {
    $("daq-mode").value = snapshot.run_config.daq.mode;
    $("trigger-mode").value = snapshot.run_config.trigger.mode;
  }
  if (snapshot.shutdown_state) {
    exiting = true;
    if (!$("exit-dialog").open && snapshot.shutdown_state !== "error")
      $("exit-dialog").showModal();
    $("confirm-exit").disabled = true;
    $("cancel-exit").disabled = snapshot.shutdown_state === "stopping";
    $("exit-progress").hidden = snapshot.shutdown_state !== "stopping";
    if (snapshot.shutdown_state === "ready") {
      exitReady = true;
      $("exit-title").textContent = "DAQ safely closed";
      $("exit-status").textContent =
        "Files closed. CAEN released. The server is shutting down; you can close this tab.";
    } else if (snapshot.shutdown_state === "error") {
      $("exit-status").textContent =
        "Safe exit could not be confirmed: " +
        snapshot.shutdown_error +
        ". Server remains available for Logs.";
    } else {
      $("exit-status").textContent =
        "Stopping acquisition and closing ROOT. Waiting for CAEN cleanup...";
    }
  }
  const s = snapshot.status || {},
    b = snapshot.board || {};
  $("state").textContent = snapshot.state || "Disconnected";
  $("state").className = "state " + snapshot.state;
  $("demo").hidden = !snapshot.demo;
  $("board-model").textContent = b.model || "DT5740D";
  $("serial").textContent = b.serial_number ?? "--";
  $("firmware").textContent = b.roc_firmware || "--";
  $("adc").textContent = (b.adc_bits || 12) + " bit";
  const c = snapshot.run_config || config;
  if (!c) return;
  $("run-id").textContent = String(s.run_id ?? c.run.run_id).padStart(6, "0");
  $("run-output").textContent =
    snapshot.output ||
    `${c.run.output_dir}/run_${String(c.run.run_id).padStart(6, "0")}.root`;
  $("total").textContent = fmt(s.total_events);
  $("written").textContent = fmt(s.written_events);
  $("elapsed").textContent = elapsed(s.elapsed_s);
  $("interval").textContent =
    `${s.rate_interval_s || c.monitor.rate_interval_s || 1} s`;
  $("interval-events").textContent = fmt(s.interval_events);
  $("file-size").textContent = bytes(s.root_size_bytes);
  $("disk-free").textContent = s.disk_free_bytes
    ? bytes(s.disk_free_bytes)
    : "--";
  $("timestamp").textContent = s.last_timestamp ?? "--";
  $("caen-errors").textContent = fmt(s.caen_errors);
  $("trigger-source").textContent = snapshot.busy
    ? c.trigger.mode
    : $("trigger-mode").selectedOptions[0].textContent;
  const progress = Math.min(
    100,
    (100 * (s.total_events || 0)) / c.run.max_events,
  );
  $("run-progress").value = progress;
  $("progress-label").textContent =
    `${fmt(s.total_events)} / ${fmt(c.run.max_events)} events`;
  $("footer-status").textContent = snapshot.error || snapshot.state || "Ready";
  const mode = snapshot.busy ? c.daq.mode : $("daq-mode").value;
  if (snapshot.busy && mode === "write_only") waves = [];
  $("run-chart-section").hidden = mode !== "full_monitor";
  $("channels-section").hidden = mode !== "full_monitor";
  $("display-state").textContent = paused
    ? "Display paused"
    : mode !== "full_monitor"
      ? "Write Only · display off"
      : `Preview ${Math.max(0, waveSequence)}`;
  $("display-holdoff").textContent =
    `Display sampled · ${c.monitor.waveform_update_interval_s || 1} s`;
  $("stats-interval").textContent =
    `Sliding window: ${s.rate_interval_s || c.monitor.rate_interval_s || 1} s`;
  $("channel-count").textContent = `${c.channels.enabled.length} enabled`;
  const rows = $("channels-table");
  rows.replaceChildren();
  for (let ch = 0; ch < 16; ch++) {
    const tr = document.createElement("tr"),
      enabled = c.channels.enabled.includes(ch),
      wave = (snapshot.preview_info || []).find((w) => w.channel === ch);
    tr.className = enabled ? "" : "disabled-row";
    [
      String(ch).padStart(2, "0"),
      Math.floor(ch / 8),
      enabled ? "Enabled" : "Disabled",
      wave ? fmt(wave.samples) : "--",
      wave ? wave.event_id : "--",
      enabled ? (s.total_events ? "Recorded" : "Ready") : "Disabled",
    ].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });
    rows.appendChild(tr);
  }
  const scan = snapshot.scan || {};
  $("scan-progress").value = scan.points
    ? (100 * (scan.counts || []).length) / scan.points
    : 0;
  $("scan-status").textContent = scan.points
    ? `${(scan.counts || []).length} / ${scan.points} points · ${scan.current_threshold} ADC · ${scan.finished ? "Finished" : "Measuring"}`
    : "Ready · ROOT recording off";
  if (snapshot.error && snapshot.error !== lastError) {
    error(snapshot.error);
    lastError = snapshot.error;
  }
  updateButtons();
  renderCharts();
}

function renderCharts() {
  const history = snapshot.history || [],
    c = snapshot.run_config || config;
  if (!c) return;
  if (snapshot.operation === "acquiring" && c.daq.mode === "write_only") return;
  const series = [
    {
      label: `Events / ${(snapshot.status || {}).rate_interval_s || c.monitor.rate_interval_s || 1} s`,
      x: history.map((p) => (p[0] - Date.now() / 1000) / 60),
      y: history.map((p) => p[1]),
      color: colors[1],
    },
  ];
  if (view === "run" && !$("run-chart-section").hidden)
    DAQCharts.draw("run-chart", series, "Minutes to now", "Events", {
      xRange: [-30, 0],
    });
  if (view === "stats")
    DAQCharts.draw("stats-chart", series, "Minutes to now", "Events", {
      xRange: [-30, 0],
    });
  if ($("scan-dialog").open) {
    const scan = snapshot.scan || {};
    DAQCharts.draw(
      "scan-chart",
      [
        {
          label: "Accepted triggers",
          x: scan.thresholds || [],
          y: scan.counts || [],
          color: colors[1],
        },
      ],
      "Threshold, ADC",
      "Events",
    );
  }
}

function renderWaves() {
  const visible = waves.filter((w) => selected.has(w.channel));
  const series = visible.map((w) => ({
    label: `ch${w.channel}`,
    x: w.x,
    y: w.y,
    color: colors[w.channel % colors.length],
  }));
  if (
    $("threshold-line").checked &&
    visible.some((w) => w.channel === 0) &&
    config
  ) {
    const n = visible.find((w) => w.channel === 0).samples;
    series.push({
      label: "Threshold",
      x: [0, n - 1],
      y: [config.threshold.value_adc, config.threshold.value_adc],
      color: "#e4bc64",
    });
  }
  DAQCharts.draw("wave-chart", series, "Samples", "ADC", {
    fixed: !$("autoscale").checked,
  });
  $("wave-metadata").textContent = visible
    .map(
      (w) =>
        `ch${w.channel} · event ${w.event_id} · timestamp ${w.timestamp} · ${w.samples} samples`,
    )
    .join("   |   ");
}
for (let ch = 0; ch < 16; ch++) {
  const label = document.createElement("label");
  label.style.borderColor = colors[ch % colors.length];
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = ch === 0;
  box.setAttribute("aria-label", `Display channel ${ch}`);
  box.onchange = () => {
    if (box.checked) selected.add(ch);
    else selected.delete(ch);
    renderWaves();
  };
  label.append(box, `ch${ch}`);
  $("channel-picker").append(label);
}
$("wave-pause").onclick = () => {
  paused = !paused;
  $("wave-pause").querySelector("span").textContent = paused
    ? "Resume display"
    : "Pause display";
  renderState();
};
$("autoscale").onchange = renderWaves;
$("threshold-line").onchange = renderWaves;
$("export-wave").onclick = () => DAQCharts.exportPNG("wave-chart");

const fields = [
  [
    "Run",
    [
      ["run_id", "Run ID", "int", 0, 2147483647],
      ["mode", "Run label", "text"],
      ["output_dir", "Output directory", "text"],
      ["max_events", "Max events", "int", 1, 2147483647],
      ["file_prefix", "Legacy prefix", "readonly"],
    ],
    "run",
  ],
  [
    "CAEN DT5740D",
    [
      ["model", "Model", "readonly"],
      ["connection", "Connection", "readonly"],
      ["firmware", "Firmware", "readonly"],
      ["link_num", "USB link", "int", 0, 100],
      ["conet_node", "CONET node", "int", 0, 100],
      ["vme_base_address", "VME address", "int", 0, 4294967295],
      ["record_length_samples", "Record length, samples", "int", 1, 196608],
      ["pre_trigger_percent", "Pre-trigger, %", "int", 0, 100],
      ["dc_offset", "Group DC offset", "int", 0, 65535],
      ["max_events_blt", "Max events BLT", "int", 1, 1024],
      ["acquisition_mode", "Acquisition", "readonly"],
    ],
    "caen",
  ],
  [
    "Channels",
    [
      ["n_channels", "Application channels", "readonly"],
      ["polarity", "Signal polarity", ["positive", "negative"]],
      ["enabled", "Record channels", "channels"],
    ],
    "channels",
  ],
  [
    "Threshold",
    [
      ["channel", "Trigger channel", "int", 0, 0],
      ["value_adc", "Threshold, ADC", "int", 0, 4095],
    ],
    "threshold",
  ],
  [
    "External / TRG-IN",
    [
      ["input", "Input", "readonly"],
      ["io_level", "Logic level", ["NIM", "TTL"]],
      ["polarity", "Edge", ["rising", "falling"]],
      ["save_all_enabled_channels", "Record enabled channels", "bool"],
    ],
    "external",
  ],
  [
    "Periodic trigger",
    [["interval_s", "Trigger interval, s", "float", 0.001, 86400]],
    "periodic",
  ],
  [
    "ROOT storage",
    [
      ["write_root", "Write ROOT", "readonly"],
      ["save_waveforms", "Save waveform", "readonly"],
      ["compression", "Compression", ["none", "zlib"]],
    ],
    "storage",
  ],
  [
    "Monitor",
    [
      ["enabled", "Periodic log", "bool"],
      ["update_every_events", "Log every events", "int", 1, 10000000],
      ["waveform_update_interval_s", "Display holdoff, s", "float", 0.05, 60],
      ["rate_interval_s", "Event count window, s", "float", 0.1, 3600],
    ],
    "monitor",
  ],
];

function buildSettings() {
  const form = $("settings-form");
  form.replaceChildren();
  for (const [title, specs, section] of fields) {
    const group = document.createElement("fieldset"),
      legend = document.createElement("legend");
    legend.textContent = title;
    group.append(legend);
    for (const [key, name, type, min, max] of specs) {
      const label = document.createElement("label");
      label.className = "setting-field";
      label.append(document.createTextNode(name));
      let input;
      if (type === "channels") {
        input = document.createElement("div");
        input.className = "channel-settings";
        for (let ch = 0; ch < 16; ch++) {
          const l = document.createElement("label"),
            box = document.createElement("input");
          box.type = "checkbox";
          box.checked = config.channels.enabled.includes(ch);
          box.dataset.channel = ch;
          box.onchange = changed;
          l.append(box, `ch${ch}`);
          input.append(l);
        }
      } else {
        input = Array.isArray(type)
          ? document.createElement("select")
          : document.createElement("input");
        input.name = section + "." + key;
        input.dataset.kind = type;
        input.setAttribute("aria-label", title + " " + name);
        if (Array.isArray(type)) {
          for (const option of type) {
            const o = document.createElement("option");
            o.value = option;
            o.textContent = option;
            input.append(o);
          }
        } else if (type === "bool") {
          input.type = "checkbox";
          input.checked = config[section][key];
        } else if (type === "int" || type === "float") {
          input.type = "number";
          input.min = min;
          input.max = max;
          input.step = type === "int" ? 1 : "any";
          input.required = true;
        } else if (type === "readonly") input.readOnly = true;
        else input.required = true;
        if (type !== "bool") input.value = config[section][key] ?? "";
        input.addEventListener("input", changed);
      }
      label.append(input);
      group.append(label);
    }
    form.append(group);
  }
  updateButtons();
}

function draft() {
  const data = structuredClone(config);
  $("settings-form")
    .querySelectorAll("[name]")
    .forEach((el) => {
      const [section, key] = el.name.split(".");
      const type = el.dataset.kind;
      if (type === "readonly") return;
      data[section][key] =
        type === "bool"
          ? el.checked
          : type === "int" || type === "float"
            ? Number(el.value)
            : el.value;
    });
  data.channels.enabled = [
    ...$("settings-form").querySelectorAll("[data-channel]:checked"),
  ].map((el) => Number(el.dataset.channel));
  return data;
}

function changed() {
  validationSequence++;
  dirty = true;
  settingsValid = false;
  const valid = $("settings-form").checkValidity();
  $("settings-status").textContent = valid
    ? "Validating changes"
    : "Invalid field value";
  $("settings-status").className = valid ? "muted" : "invalid";
  updateButtons();
  clearTimeout(validationTimer);
  validationTimer = setTimeout(validateDraft, 300);
}
async function validateDraft() {
  const seq = ++validationSequence;
  if (!$("settings-form").checkValidity()) return;
  try {
    await api("/config/validate", "POST", {
      data: draft(),
      revision,
    });
    if (seq !== validationSequence) return;
    settingsValid = true;
    $("settings-status").textContent = dirty
      ? "Validated · unsaved changes"
      : "Settings saved";
    $("settings-status").className = "valid";
  } catch (e) {
    if (seq !== validationSequence) return;
    settingsValid = false;
    $("settings-status").textContent = e.message;
    $("settings-status").className = "invalid";
  }
  updateButtons();
}
async function loadSettings(initial = false) {
  validationSequence++;
  clearTimeout(validationTimer);
  try {
    const result = await api("/config");
    config = result.data;
    revision = result.revision;
    dirty = false;
    settingsValid = true;
    buildSettings();
    $("settings-status").textContent = "Settings saved";
    $("settings-status").className = "valid";
    if (initial) {
      $("daq-mode").value = config.daq.mode;
      $("trigger-mode").value = config.trigger.mode;
    }
    renderState();
  } catch (e) {
    error(e.message);
  }
}
$("reload-settings").onclick = () => loadSettings();
$("save-settings").onclick = async () => {
  if (pending) return;
  pending = true;
  validationSequence++;
  clearTimeout(validationTimer);
  updateButtons();
  try {
    const result = await api("/config", "PUT", {
      data: draft(),
      revision,
    });
    config = result.data;
    revision = result.revision;
    dirty = false;
    settingsValid = true;
    $("settings-status").textContent = "Settings saved";
    $("settings-status").className = "valid";
    updateButtons();
    renderState();
  } catch (e) {
    error(e.message);
  } finally {
    pending = false;
    updateButtons();
  }
};
$("settings-form").onsubmit = (e) => e.preventDefault();
$("yaml-upload").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = new FormData();
    data.append("file", file);
    const response = await fetch("/api/config/import", {
      method: "POST",
      body: data,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail);
    config = result.data;
    buildSettings();
    changed();
  } catch (e) {
    error(e.message);
  }
  e.target.value = "";
};

async function refreshFiles() {
  try {
    const files = await api("/files"),
      value = $("root-files").value;
    $("root-files").replaceChildren(new Option("Select a ROOT file", ""));
    for (const file of files) {
      const option = new Option(
        `${file.path} · ${bytes(file.bytes)}${file.active ? " · RECORDING" : ""}`,
        file.path,
      );
      option.disabled = file.active;
      $("root-files").append(option);
    }
    $("root-files").value = rootPath || value;
  } catch (e) {
    error(e.message);
  }
}
async function loadRoot(path) {
  if (!path) return;
  const seq = ++rootRequest;
  entryRequest++;
  $("viewer-progress").hidden = false;
  $("viewer-task").textContent = "Reading ROOT metadata";
  try {
    const result = await api("/root/summary?path=" + encodeURIComponent(path));
    if (seq !== rootRequest) return;
    rootSummary = result;
    rootPath = path;
    $("root-entry").max = Math.max(0, rootSummary.rows - 1);
    $("root-entry").value = 0;
    $("root-info").textContent =
      `${fmt(rootSummary.events)} events · ${fmt(rootSummary.rows)} waveforms · ${bytes(rootSummary.bytes)}`;
    $("download-root").href =
      "/api/root/download?path=" + encodeURIComponent(path);
    $("download-root").hidden = false;
    DAQCharts.draw(
      "offline-rate",
      [
        {
          label: "Physical events",
          x: rootSummary.rate_x,
          y: rootSummary.rate_y,
        },
      ],
      "Relative timestamp, ticks",
      "Events / bin",
    );
    if (rootSummary.rows) await showEntry();
    else {
      DAQCharts.draw("offline-wave", [], "Samples", "ADC");
      $("offline-metadata").textContent = "Empty ROOT file";
    }
  } catch (e) {
    error(e.message);
  } finally {
    if (seq === rootRequest) $("viewer-progress").hidden = true;
  }
}
let entryRequest = 0,
  rootRequest = 0;
async function showEntry() {
  if (!rootPath || !rootSummary?.rows) return;
  const seq = ++entryRequest;
  try {
    const wave = await api(
      "/root/waveform?path=" +
        encodeURIComponent(rootPath) +
        "&entry=" +
        Number($("root-entry").value),
    );
    if (seq !== entryRequest) return;
    DAQCharts.draw(
      "offline-wave",
      [
        {
          label: `ch${wave.channel}`,
          x: wave.x,
          y: wave.y,
        },
      ],
      "Samples",
      "ADC",
    );
    $("offline-metadata").textContent =
      `ch${wave.channel} · event ${wave.event_id} · timestamp ${wave.timestamp} · ${fmt(wave.samples)} samples`;
  } catch (e) {
    error(e.message);
  }
}
$("refresh-files").onclick = refreshFiles;
$("load-root").onclick = () => loadRoot($("root-files").value);
$("show-entry").onclick = showEntry;
for (const [id, delta] of [
  ["previous", -1],
  ["next", 1],
])
  $(id).onclick = () => {
    if (!rootSummary) return;
    $("root-entry").value = Math.min(
      rootSummary.rows - 1,
      Math.max(0, Number($("root-entry").value) + delta),
    );
    showEntry();
  };
$("root-upload").onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const xhr = new XMLHttpRequest(),
    form = new FormData();
  form.append("file", file);
  $("viewer-progress").hidden = false;
  const progress = $("viewer-progress").querySelector("progress");
  progress.max = 100;
  progress.value = 0;
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      progress.value = (100 * e.loaded) / e.total;
      $("viewer-task").textContent = `Uploading ${progress.value.toFixed(0)}%`;
    }
  };
  xhr.onload = async () => {
    try {
      const result = JSON.parse(xhr.responseText);
      if (xhr.status >= 400) throw new Error(result.detail);
      await refreshFiles();
      $("root-files").value = result.path;
      await loadRoot(result.path);
    } catch (e) {
      error(e.message);
    } finally {
      $("viewer-progress").hidden = true;
      progress.removeAttribute("value");
    }
  };
  xhr.onerror = () => {
    error("Upload failed: connection lost");
    $("viewer-progress").hidden = true;
  };
  xhr.open("POST", "/api/root/upload");
  xhr.send(form);
  e.target.value = "";
};

$("open-scan").onclick = () => {
  $("scan-dialog").showModal();
  renderCharts();
};
$("close-scan").onclick = () => {
  $("scan-dialog").close();
};
$("scan-form").onsubmit = (e) => {
  e.preventDefault();
  const values = Object.fromEntries(
    [...new FormData(e.target)].map(([k, v]) => [k, Number(v)]),
  );
  command("/scan/start", values);
};
$("scan-stop").onclick = () => command("/run/stop");
$("use-threshold").onclick = () => {
  const input = $("selected-threshold");
  if (!input.reportValidity() || !config) return;
  const field = $("settings-form").querySelector(
    '[name="threshold.value_adc"]',
  );
  field.value = Number(input.value);
  changed();
  $("scan-dialog").close();
  setView("settings");
};
$("export-scan").onclick = () => {
  const scan = snapshot.scan || {};
  download(
    "threshold-scan.csv",
    "threshold_adc,events,rate_hz\n" +
      (scan.thresholds || [])
        .map((v, i) => `${v},${scan.counts[i]},${scan.rates_hz[i]}`)
        .join("\n"),
    "text/csv",
  );
};

function renderLogs() {
  const level = $("log-level").value,
    rows = logRows.filter(
      (row) =>
        level === "all" ||
        (level === "WARNING" &&
          ["WARNING", "ERROR", "CRITICAL"].includes(row.level)) ||
        row.level === level,
    );
  const el = $("logs"),
    atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
  el.replaceChildren();
  for (const row of rows) {
    const line = document.createElement("div");
    line.className = "log-row " + row.level;
    line.textContent = `${row.time}  ${row.level.padEnd(7)}  ${row.message}`;
    el.append(line);
  }
  if (atBottom) el.scrollTop = el.scrollHeight;
  $("recent-log").textContent =
    logRows
      .slice(-4)
      .map((row) => `${row.time}  ${row.level}  ${row.message}`)
      .join("\n") || "Waiting for an operation";
}
$("log-level").onchange = renderLogs;
$("export-logs").onclick = () =>
  download(
    "telescopedaq.log",
    logRows.map((row) => `${row.time} ${row.level} ${row.message}`).join("\n"),
  );
async function pollState() {
  snapshot = await api("/state");
  renderState();
}
async function poll() {
  try {
    if (!document.hidden) {
      await pollState();
      const rows = await api("/logs?after=" + logId);
      if (rows.length) {
        logId = rows.at(-1).id;
        logRows.push(...rows);
        logRows = logRows.slice(-1000);
        renderLogs();
      }
      const c = snapshot.run_config || config,
        interval = c?.monitor.waveform_update_interval_s || 1;
      if (
        view === "wave" &&
        !paused &&
        c?.daq.mode === "full_monitor" &&
        Date.now() - lastWaveFetch >= interval * 1000
      ) {
        const preview = await api("/waveforms?after=" + waveSequence);
        if (preview.channels !== null) {
          waveSequence = preview.sequence;
          waves = preview.channels;
          renderWaves();
        }
        lastWaveFetch = Date.now();
      }
    }
    $("api-status").textContent = "Server connected";
    $("api-dot").classList.add("online");
  } catch (e) {
    $("api-status").textContent = exitReady
      ? "Server stopped"
      : "Server unavailable";
    $("api-dot").classList.remove("online");
    $("footer-status").textContent = exitReady
      ? "Safe exit complete"
      : e.message;
    if (exitReady) return;
  }
  $("clock").textContent = new Date().toLocaleTimeString("ru-RU");
  setTimeout(poll, 500);
}
icons();
loadSettings(true).then(poll);

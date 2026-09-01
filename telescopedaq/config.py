from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DAQConfig:
    source: Path
    data: dict[str, Any]

    @property
    def run(self) -> dict[str, Any]:
        return self.data["run"]

    @property
    def daq(self) -> dict[str, Any]:
        return self.data["daq"]

    @property
    def caen(self) -> dict[str, Any]:
        return self.data["caen"]

    @property
    def channels(self) -> dict[str, Any]:
        return self.data["channels"]

    @property
    def trigger(self) -> dict[str, Any]:
        return self.data["trigger"]


def load_config(
    path: str | Path,
    daq_mode: str | None = None,
    trigger_mode: str | None = None,
) -> DAQConfig:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Конфигурация не найдена: {source}")
    with source.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("Корень YAML должен быть отображением")
    data = copy.deepcopy(data)
    if daq_mode is not None:
        data.setdefault("daq", {})["mode"] = daq_mode
        data.setdefault("monitor", {})["draw_waveforms"] = daq_mode == "full_monitor"
    if trigger_mode is not None:
        data.setdefault("trigger", {})["mode"] = trigger_mode
        for section in ("threshold", "external", "periodic"):
            data.setdefault(section, {})["enabled"] = section == trigger_mode
    required = {
        "run", "daq", "caen", "channels", "trigger", "threshold",
        "external", "periodic", "storage", "monitor",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"В YAML отсутствуют разделы: {', '.join(missing)}")
    enabled = data["channels"].get("enabled", [])
    if not isinstance(enabled, list) or not enabled or any(not isinstance(channel, int) or not 0 <= channel < 16 for channel in enabled) or len(set(enabled)) != len(enabled):
        raise ValueError("channels.enabled должен содержать уникальные каналы 0..15")
    if data["caen"].get("firmware", "STANDARD").upper() != "STANDARD":
        raise ValueError("DT5740D v0.1 требует firmware: STANDARD")
    if data["caen"].get("model") != "DT5740D":
        raise ValueError("TelescopeDAQ поддерживает только caen.model: DT5740D")
    if data["caen"].get("connection") != "USB":
        raise ValueError("CAEN DT5740D должен использовать caen.connection: USB")
    if int(data["run"].get("run_id", -1)) < 0 or int(data["run"].get("max_events", 0)) <= 0:
        raise ValueError("run_id должен быть >= 0, max_events должен быть > 0")
    if not str(data["run"].get("output_dir", "")).strip():
        raise ValueError("run.output_dir не должен быть пустым")
    caen = data["caen"]
    if int(caen.get("link_num", -1)) < 0 or int(caen.get("conet_node", -1)) < 0:
        raise ValueError("caen.link_num и caen.conet_node должны быть >= 0")
    if not 1 <= int(caen.get("record_length_samples", 0)) <= 196608:
        raise ValueError("caen.record_length_samples должен быть в диапазоне 1..196608")
    if not 0 <= int(caen.get("pre_trigger_percent", -1)) <= 100:
        raise ValueError("caen.pre_trigger_percent должен быть в диапазоне 0..100")
    if not 0 <= int(caen.get("dc_offset", -1)) <= 65535:
        raise ValueError("caen.dc_offset должен быть в диапазоне 0..65535")
    if int(caen.get("max_events_blt", 0)) <= 0:
        raise ValueError("caen.max_events_blt должен быть > 0")
    polarity = data["channels"].get("polarity")
    if polarity not in {"positive", "negative"}:
        raise ValueError("channels.polarity должен быть positive или negative")
    if int(data["channels"].get("n_channels", 0)) != 16:
        raise ValueError("CAEN DT5740D должен иметь channels.n_channels: 16")
    daq_mode = str(data["daq"].get("mode", ""))
    if daq_mode not in {"full_monitor", "write_only", "root_viewer"}:
        raise ValueError("daq.mode должен быть full_monitor, write_only или root_viewer")
    trigger_mode = str(data["trigger"].get("mode", ""))
    if trigger_mode not in {"threshold", "external", "periodic"}:
        raise ValueError("trigger.mode должен быть threshold, external или periodic")
    threshold_value = int(data["threshold"].get("value_adc", -1))
    if int(data["threshold"].get("channel", -1)) != 0 or not 0 <= threshold_value <= 4095:
        raise ValueError("threshold требует channel: 0 и value_adc в диапазоне 0..4095")
    external = data["external"]
    if external.get("input") != "TRG-IN" or external.get("polarity") not in {"rising", "falling"}:
        raise ValueError("external требует input: TRG-IN и polarity rising/falling")
    if float(data["periodic"].get("interval_s", 0)) <= 0:
        raise ValueError("periodic.interval_s должен быть > 0")
    if trigger_mode == "threshold":
        threshold = data["threshold"]
        if not threshold.get("enabled"):
            raise ValueError("threshold.enabled должен быть true для threshold trigger")
        if int(threshold.get("channel", -1)) != 0:
            raise ValueError("TelescopeDAQ v0.1 поддерживает threshold только для канала 0")
    elif trigger_mode == "external":
        external = data["external"]
        if not external.get("enabled") or external.get("input") != "TRG-IN":
            raise ValueError("Для external trigger задайте enabled: true и input: TRG-IN")
    else:
        periodic = data["periodic"]
        if not periodic.get("enabled") or float(periodic.get("interval_s", 0)) <= 0:
            raise ValueError("Для periodic trigger задайте enabled: true и interval_s > 0")
    storage = data["storage"]
    if not storage.get("write_root") or not storage.get("save_waveforms"):
        raise ValueError("Текущая версия требует storage.write_root/save_waveforms: true")
    if str(storage.get("compression", "")).lower() not in {"zlib", "none"}:
        raise ValueError("storage.compression должен быть zlib или none")
    if int(data["monitor"].get("update_every_events", 0)) <= 0:
        raise ValueError("monitor.update_every_events должен быть > 0")
    waveform_interval = float(data["monitor"].get("waveform_update_interval_s", 1.0))
    if not 0.05 <= waveform_interval <= 60.0:
        raise ValueError("monitor.waveform_update_interval_s должен быть в диапазоне 0.05..60 s")
    rate_interval = float(data["monitor"].get("rate_interval_s", 1.0))
    if not 0.1 <= rate_interval <= 3600.0:
        raise ValueError("monitor.rate_interval_s должен быть в диапазоне 0.1..3600 s")
    return DAQConfig(source=source, data=data)

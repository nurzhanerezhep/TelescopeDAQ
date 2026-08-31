from __future__ import annotations

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
    def caen(self) -> dict[str, Any]:
        return self.data["caen"]

    @property
    def channels(self) -> dict[str, Any]:
        return self.data["channels"]

    @property
    def trigger(self) -> dict[str, Any]:
        return self.data["trigger"]


def load_config(path: str | Path) -> DAQConfig:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Конфигурация не найдена: {source}")
    with source.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("Корень YAML должен быть отображением")
    required = {"run", "caen", "channels", "trigger", "storage", "monitor"}
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"В YAML отсутствуют разделы: {', '.join(missing)}")
    enabled = data["channels"].get("enabled", [])
    if enabled != [0]:
        raise ValueError("TelescopeDAQ v0.1 поддерживает только channels.enabled: [0]")
    if data["caen"].get("firmware", "STANDARD").upper() != "STANDARD":
        raise ValueError("DT5740D v0.1 требует firmware: STANDARD")
    polarity = data["channels"].get("polarity")
    if polarity not in {"positive", "negative"}:
        raise ValueError("channels.polarity должен быть positive или negative")
    return DAQConfig(source=source, data=data)

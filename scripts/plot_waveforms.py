from __future__ import annotations

import argparse
import os
from pathlib import Path

import awkward as ak

PROJECT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".matplotlib"))

import matplotlib.pyplot as plt
import numpy as np
import uproot


def main() -> int:
    parser = argparse.ArgumentParser(description="Построение waveform из ROOT")
    parser.add_argument("root_file", type=Path)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()
    with uproot.open(args.root_file) as root:
        arrays = root["events"].arrays(["channel", "event_id", "waveform"])
    selected = arrays[arrays.channel == args.channel][: args.n]
    if len(selected) == 0:
        raise RuntimeError(f"В файле нет событий канала {args.channel}")
    for event_id, waveform in zip(ak.to_numpy(selected.event_id), selected.waveform):
        plt.plot(np.arange(len(waveform)) * 16, ak.to_numpy(waveform), alpha=0.65, label=f"event {event_id}")
    plt.xlabel("Время, нс")
    plt.ylabel("ADC count")
    plt.title(f"DT5740D, канал {args.channel}")
    if len(selected) <= 10:
        plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    output = args.root_file.with_name(f"{args.root_file.stem}_waveforms_ch{args.channel}.png")
    plt.savefig(output, dpi=150)
    print(f"PNG сохранён: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

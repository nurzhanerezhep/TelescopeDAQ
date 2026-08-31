from __future__ import annotations

import argparse
from pathlib import Path

import awkward as ak
import numpy as np
import uproot


def main() -> int:
    parser = argparse.ArgumentParser(description="Инспекция ROOT-файла TelescopeDAQ")
    parser.add_argument("root_file", type=Path)
    args = parser.parse_args()
    with uproot.open(args.root_file) as root:
        print("Объекты:", root.keys())
        tree = root["events"]
        print("Число entries:", tree.num_entries)
        if tree.num_entries == 0:
            return 0
        arrays = tree.arrays(["event_id", "channel", "timestamp", "baseline", "amplitude", "charge", "waveform"])
        lengths = ak.to_numpy(ak.num(arrays.waveform))
        print("Каналы:", np.unique(ak.to_numpy(arrays.channel)).tolist())
        print("Первые 5 событий:")
        for row in arrays[:5].to_list():
            row["waveform"] = f"{len(row['waveform'])} samples"
            print(row)
        timestamps = ak.to_numpy(arrays.timestamp)
        amplitudes = ak.to_numpy(arrays.amplitude)
        print(f"Timestamp min/max: {timestamps.min()} / {timestamps.max()}")
        print(f"Waveform length min/max/mean: {lengths.min()} / {lengths.max()} / {lengths.mean():.1f}")
        print(f"Amplitude min/max/mean: {amplitudes.min():.2f} / {amplitudes.max():.2f} / {amplitudes.mean():.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

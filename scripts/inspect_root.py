from __future__ import annotations

import argparse
from pathlib import Path

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
        channels, first, last, triggers, previous = set(), None, None, 0, None
        # Stream metadata; load samples only for the first five waveform entries.
        for batch in tree.iterate(
            ["event_id", "channel", "timestamp"], step_size="8 MB", library="np"
        ):
            channels.update(np.unique(batch["channel"]).tolist())
            ids = batch["event_id"]
            if ids.size:
                triggers += int(ids[0] != previous) + int(
                    np.count_nonzero(ids[1:] != ids[:-1])
                )
                previous = int(ids[-1])
                low, high = int(batch["timestamp"].min()), int(batch["timestamp"].max())
                first = low if first is None else min(first, low)
                last = high if last is None else max(last, high)
        print("Каналы:", sorted(channels))
        print("Physical events:", triggers)
        print(f"Timestamp min/max (raw ticks): {first} / {last}")
        arrays = tree.arrays(
            ["event_id", "channel", "timestamp", "trigger_type", "waveform"],
            entry_stop=5,
        )
        print("Первые 5 событий:")
        for row in arrays[:5].to_list():
            row["waveform"] = f"{len(row['waveform'])} samples"
            print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from telescopedaq.gui import launch_gui


def main() -> int:
    parser = argparse.ArgumentParser(description="TelescopeDAQ graphical monitor")
    parser.add_argument("--config", type=Path, default=Path("configs/channel0_generator_test.yaml"))
    args = parser.parse_args()
    launch_gui(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

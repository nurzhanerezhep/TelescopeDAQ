from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from telescopedaq.run_control import start_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Запуск TelescopeDAQ с реальным CAEN DT5740D")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--max-events", type=int, help="временное ограничение для короткого теста")
    args = parser.parse_args()
    if args.max_events is not None and args.max_events <= 0:
        parser.error("--max-events должен быть больше нуля")
    start_run(args.config, args.max_events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

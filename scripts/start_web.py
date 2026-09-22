from __future__ import annotations

import argparse
import logging
import socket
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def main():
    parser = argparse.ArgumentParser(description="TelescopeDAQ FastAPI control panel")
    parser.add_argument(
        "--config", type=Path, default=PROJECT / "configs/channel0_generator_test.yaml"
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo", action="store_true", help="Synthetic source, no CAEN access"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from telescopedaq.web.app import create_app
    import uvicorn

    # One process owns the hardware. Never use reload or multiple Uvicorn workers.
    listener = socket.socket()
    for port in range(args.port, min(args.port + 20, 65536)):
        try:
            listener.bind(("127.0.0.1", port))
            break
        except OSError:
            continue
    else:
        raise RuntimeError("No available port")
    listener.listen(128)
    logging.info(
        "TelescopeDAQ%s: http://127.0.0.1:%d", " DEMO" if args.demo else "", port
    )
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(args.config, PROJECT, args.demo),
                access_log=False,
                log_level="info",
            )
        )
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()

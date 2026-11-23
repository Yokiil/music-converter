"""Run the development API on loopback only."""
from __future__ import annotations

import argparse
import os


def port_number(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the music converter API (localhost only)")
    parser.add_argument("--port", type=port_number, default=os.environ.get("MUSIC_PORT", "8787"))
    args = parser.parse_args()
    try:
        import uvicorn
    except ImportError:
        parser.exit(1, "Install dependencies: python -m pip install -r requirements-server.txt\n")
    uvicorn.run("server.api:app", host="127.0.0.1", port=args.port, access_log=False, http="h11",
                proxy_headers=False, server_header=False, timeout_keep_alive=5, timeout_graceful_shutdown=20)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Start the central orchestration server.

Usage:
    python scripts/start_central.py [--host 0.0.0.0] [--port 8000]
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from uiAutoAgent.central.server import start


def main() -> None:
    parser = argparse.ArgumentParser(description="uiAutoAgent Central Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()
    start(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

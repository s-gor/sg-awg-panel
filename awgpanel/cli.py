from __future__ import annotations

import argparse
import json

from . import __version__
from .core import get_awg_overview
from .db import init_db


def main() -> int:
    parser = argparse.ArgumentParser(prog="awgpanel")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init-db")
    sub.add_parser("status")
    args = parser.parse_args()

    if args.version:
        print(f"SG-AWG-Panel {__version__}")
        return 0
    if args.command == "init-db":
        init_db()
        print("Database initialized")
        return 0
    if args.command == "status":
        overview = get_awg_overview()
        print(json.dumps({
            "installed": overview["installed"],
            "module_loaded": overview["module_loaded"],
            "configured": overview["configured"],
            "service_state": overview["service_state"],
            "clients": len(overview["clients"]),
        }, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 2

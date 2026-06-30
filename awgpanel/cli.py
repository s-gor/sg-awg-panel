from __future__ import annotations

import argparse
import json

from . import __version__
from .core import client_expiry_tick, create_manual_backup, ensure_default_awg_server, get_awg_overview
from .egress import apply_egress_runtime, clear_egress_runtime, traffic_runtime_status
from .db import init_db
from .traffic_rules import traffic_schedule_tick


def main() -> int:
    parser = argparse.ArgumentParser(prog="awgpanel")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init-db")
    sub.add_parser("status")
    sub.add_parser("backup")
    sub.add_parser("apply-traffic")
    sub.add_parser("clear-traffic")
    sub.add_parser("traffic-status")
    sub.add_parser("traffic-tick")
    sub.add_parser("clients-tick")
    sub.add_parser("ensure-server")
    operation = sub.add_parser("operation-job")
    operation.add_argument("--token", required=True)
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
    if args.command == "backup":
        backup = create_manual_backup()
        print(backup)
        return 0
    if args.command == "apply-traffic":
        print(json.dumps(apply_egress_runtime(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "clear-traffic":
        clear_egress_runtime()
        print("Traffic Rules runtime cleared")
        return 0
    if args.command == "traffic-status":
        print(json.dumps(traffic_runtime_status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "traffic-tick":
        print(json.dumps(traffic_schedule_tick(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "clients-tick":
        print(json.dumps(client_expiry_tick(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ensure-server":
        print(json.dumps(ensure_default_awg_server(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "operation-job":
        from .operation_jobs import run_operation_job
        return run_operation_job(args.token)
    parser.print_help()
    return 2

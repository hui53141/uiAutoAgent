#!/usr/bin/env python3
"""
Dispatch a task to executor nodes (or all nodes).

Usage:
    # Run a task on specific nodes
    python scripts/run_task.py --task smoke-login --nodes node-01 node-02

    # Run a task on all registered nodes
    python scripts/run_task.py --task regression-settings --all-nodes

    # Generate a new AW class
    python scripts/run_task.py --generate-aw --page checkout \
        --class-name CheckoutAW \
        --operations "add item to cart" "proceed to checkout" "confirm order"
"""

import argparse
import sys
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from uiAutoAgent.core import setup_logging

logger = setup_logging("run_task")


def load_task_config(task_id: str) -> dict:
    cfg_path = Path(__file__).parent.parent / "configs" / "tasks.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for task in cfg.get("tasks", []):
        if task["id"] == task_id:
            return task
    raise ValueError(
        f"Task '{task_id}' not found. Available: {[t['id'] for t in cfg.get('tasks', [])]}"
    )


def get_registered_nodes(central_url: str) -> list:
    resp = requests.get(f"{central_url}/nodes", timeout=10)
    resp.raise_for_status()
    return [n["node_id"] for n in resp.json().get("nodes", [])]


def dispatch_task(central_url: str, task_cfg: dict, node_ids: list) -> None:
    payload = {
        "task_id": task_cfg["id"],
        "aw_class": task_cfg["aw_class"],
        "method": task_cfg["method"],
        "params": task_cfg.get("params", {}),
        "device_serial": "",  # executor assigns based on available devices
        "node_ids": node_ids,
    }
    resp = requests.post(f"{central_url}/tasks/dispatch", json=payload, timeout=10)
    resp.raise_for_status()
    logger.info("Dispatched: %s", resp.json())


def generate_aw(central_url: str, page: str, class_name: str, operations: list) -> None:
    payload = {
        "page": page,
        "class_name": class_name,
        "operations": operations,
    }
    resp = requests.post(f"{central_url}/generate/aw", json=payload, timeout=30)
    resp.raise_for_status()
    logger.info("Generation started: %s", resp.json())


def main() -> None:
    parser = argparse.ArgumentParser(description="uiAutoAgent Task Runner")
    parser.add_argument("--central-url", default="http://localhost:8000")

    sub = parser.add_subparsers(dest="command")

    # Dispatch task
    run_p = sub.add_parser("run", help="Dispatch a task to executor nodes")
    run_p.add_argument("--task", required=True, help="Task ID from configs/tasks.yaml")
    run_p.add_argument("--nodes", nargs="+", help="Node IDs to target")
    run_p.add_argument("--all-nodes", action="store_true", help="Target all registered nodes")

    # Generate AW
    gen_p = sub.add_parser("generate-aw", help="Generate a new AW class via LLM")
    gen_p.add_argument("--page", required=True)
    gen_p.add_argument("--class-name", required=True)
    gen_p.add_argument("--operations", nargs="+", required=True)

    # Status
    sub.add_parser("status", help="Show central server status")

    args = parser.parse_args()

    if args.command == "run":
        task_cfg = load_task_config(args.task)
        if args.all_nodes:
            node_ids = get_registered_nodes(args.central_url)
        elif args.nodes:
            node_ids = args.nodes
        else:
            node_ids = task_cfg.get("target_nodes", [])
        if not node_ids:
            logger.error("No nodes specified. Use --nodes or --all-nodes.")
            sys.exit(1)
        dispatch_task(args.central_url, task_cfg, node_ids)

    elif args.command == "generate-aw":
        generate_aw(
            args.central_url,
            args.page,
            args.class_name,
            args.operations,
        )

    elif args.command == "status":
        resp = requests.get(f"{args.central_url}/health", timeout=5)
        resp.raise_for_status()
        import json
        print(json.dumps(resp.json(), indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

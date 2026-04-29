#!/usr/bin/env python3
"""
Start an executor agent on this PC node.

Usage:
    python scripts/start_executor.py --node-id node-01 [--central-url http://localhost:8000]

Each executor PC should run this script with its own --node-id.
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from uiAutoAgent.core import get_settings, setup_logging
from uiAutoAgent.executor.agent import ExecutorAgent
from uiAutoAgent.executor.device_manager import DeviceManager
from uiAutoAgent.executor.drivers.appium_driver import AppiumDriver

logger = setup_logging("start_executor")


def load_node_config(node_id: str) -> dict:
    """Load device config for this node from configs/nodes.yaml."""
    cfg_path = Path(__file__).parent.parent / "configs" / "nodes.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        all_nodes = yaml.safe_load(fh)

    for node in all_nodes.get("nodes", []):
        if node["id"] == node_id:
            return node
    raise ValueError(
        f"Node '{node_id}' not found in configs/nodes.yaml. "
        f"Available: {[n['id'] for n in all_nodes.get('nodes', [])]}"
    )


def register_devices(agent: ExecutorAgent, node_config: dict) -> None:
    """Register all devices listed in the node config."""
    settings = get_settings()
    dm_cfg = settings["executor"]["device_manager"]
    appium_host = dm_cfg.get("appium_host", "localhost")
    appium_port = dm_cfg.get("appium_port", 4723)
    appium_url = f"http://{appium_host}:{appium_port}"

    for device in node_config.get("devices", []):
        serial = device["serial"]
        caps = {
            "platformName": "Android",
            "deviceName": device.get("name", serial),
            "udid": serial,
            "automationName": "UiAutomator2",
            "appPackage": settings["locator"]["app_package"],
            "appActivity": ".MainActivity",
            "noReset": True,
        }

        def make_driver(s=serial, c=caps, url=appium_url):
            return AppiumDriver(device_serial=s, capabilities=c, appium_url=url)

        agent.device_manager.register(serial, make_driver)
        logger.info("Registered device: %s (%s)", serial, device.get("name", ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="uiAutoAgent Executor Node")
    parser.add_argument(
        "--node-id",
        required=True,
        help="Unique node ID (must match configs/nodes.yaml)",
    )
    parser.add_argument(
        "--central-url",
        default="http://localhost:8000",
        help="Central server URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--sync-interval",
        type=int,
        default=60,
        help="Git pull interval in seconds (default: 60)",
    )
    args = parser.parse_args()

    logger.info("Starting executor for node: %s", args.node_id)

    node_config = load_node_config(args.node_id)
    agent = ExecutorAgent(
        node_id=args.node_id,
        central_url=args.central_url,
        sync_interval=args.sync_interval,
    )
    register_devices(agent, node_config)
    agent.start()


if __name__ == "__main__":
    main()

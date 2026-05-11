from __future__ import annotations

import asyncio
import json

import websockets

from uiAutoAgent.core import setup_logging

logger = setup_logging("ExecutorWSClient")


class WSClient:
    """
    Persistent WebSocket connection to Central control plane.
    Handles reconnection with exponential backoff.
    """

    def __init__(self, central_url: str, node_id: str, hot_patcher: "HotPatcher"):
        self.central_url = central_url.rstrip("/")
        self.node_id = node_id
        self.hot_patcher = hot_patcher
        self._connection = None
        self._send_lock = asyncio.Lock()

    async def connect_and_listen(self) -> None:
        ws_url = self.central_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/{self.node_id}"
        backoff = 1
        while True:
            try:
                async with websockets.connect(ws_url) as websocket:
                    logger.info("Connected to central websocket: %s", ws_url)
                    self._connection = websocket
                    backoff = 1
                    async for raw_message in websocket:
                        message = json.loads(raw_message)
                        await self._handle_message(message)
            except Exception as exc:
                logger.warning("WebSocket connection dropped: %s", exc)
                self._connection = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def send(self, message: dict) -> None:
        async with self._send_lock:
            if self._connection is None:
                logger.warning("WebSocket not connected; dropping message %s", message.get("type"))
                return
            await self._connection.send(json.dumps(message, ensure_ascii=False))

    async def _handle_message(self, msg: dict) -> None:
        if msg["type"] == "script_update":
            result = await self.hot_patcher.apply_and_rerun(msg)
            await self.send(result)
        elif msg["type"] == "heal_failed":
            logger.warning("Heal failed for batch %s: %s", msg["batch_id"], msg["reason"])

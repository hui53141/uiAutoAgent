from __future__ import annotations

import asyncio
from typing import Dict

from fastapi import WebSocket


class WSManager:
    """Manages persistent WebSocket connections from executor nodes."""

    def __init__(self):
        self._connections: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, node_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[node_id] = websocket

    def disconnect(self, node_id: str) -> None:
        self._connections.pop(node_id, None)

    async def send(self, node_id: str, message: dict) -> bool:
        websocket = self._connections.get(node_id)
        if websocket is None:
            return False
        await websocket.send_json(message)
        return True

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            items = list(self._connections.items())
        for node_id, websocket in items:
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(node_id)

    def is_connected(self, node_id: str) -> bool:
        return node_id in self._connections

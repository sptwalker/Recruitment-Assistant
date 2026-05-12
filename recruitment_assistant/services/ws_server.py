"""WebSocket server for Chrome Extension communication."""

import asyncio
import json
import threading
from typing import Callable

from loguru import logger


class BossWSServer:
    def __init__(self, host: str = "localhost", port: int = 8765):
        self.host = host
        self.port = port
        self.extension_ws = None
        self.on_event: Callable[[dict], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._command_queue: asyncio.Queue | None = None

    @property
    def is_extension_connected(self) -> bool:
        return self.extension_ws is not None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        deadline = threading.Event()
        for _ in range(50):
            if self._loop is not None and self._loop.is_running():
                break
            deadline.wait(0.1)
        logger.info("Boss WS 服务已启动: ws://{}:{}", self.host, self.port)

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._loop = None
        self.extension_ws = None
        logger.info("Boss WS 服务已停止")

    def send_command(self, command: dict) -> None:
        if not self.is_extension_connected:
            logger.warning("扩展未连接，无法发送指令: {}", command.get("type"))
            return
        if self._loop and self._command_queue:
            self._loop.call_soon_threadsafe(self._command_queue.put_nowait, command)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._command_queue = asyncio.Queue()
        self._shutdown_event = asyncio.Event()
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        import websockets

        async with websockets.serve(self._handler, self.host, self.port) as server:
            self._server = server
            await self._shutdown_event.wait()

    async def _handler(self, websocket) -> None:
        if self.extension_ws is not None:
            try:
                await self.extension_ws.close()
            except Exception:
                pass
        self.extension_ws = websocket
        logger.info("Chrome 扩展已连接")

        send_task = asyncio.create_task(self._send_loop(websocket))
        try:
            async for message in websocket:
                try:
                    event = json.loads(message)
                    if self.on_event:
                        self.on_event(event)
                except json.JSONDecodeError:
                    logger.warning("收到无效 JSON: {}", message[:100])
        except Exception as exc:
            logger.info("扩展连接断开: {}", exc)
        finally:
            send_task.cancel()
            if self.extension_ws is websocket:
                self.extension_ws = None
                logger.info("Chrome 扩展已断开")

    async def _send_loop(self, websocket) -> None:
        while True:
            command = await self._command_queue.get()
            try:
                await websocket.send(json.dumps(command, ensure_ascii=False))
            except Exception:
                break

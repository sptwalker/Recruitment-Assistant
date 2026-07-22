"""采集传输中枢（M2.5）——把「扩展连 localhost 单例」改成「扩展连服务器、按用户路由」。

背景：原 ws_server.py 的三个 WS 服务各监听本机固定端口（8765/66/67），单连接模型
（新连接踢旧连接），只在用户本机跑 Streamlit 时成立。应用上服务器后 localhost 断链 →
采集必须改为「扩展带 JWT 连服务器 wss，服务端按 user_id 路由消息」。本模块是该模型的
服务端内存注册表：谁的扩展在线、命令发给谁，租户隔离沿用 HTTP 层同一套 tenancy 上下文。

ponytail: 进程内内存表，单 worker 足够（扩展断线自动重连）。要多 worker 扇出再上
Redis/DB 会话表——现在没这需求，不建。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class Conn:
    ws: Any            # starlette WebSocket（send_json 可用）；测试传假对象
    platform: str      # boss / qiancheng / zhilian ...；命令按平台定向


class CrawlHub:
    """user_id → 该用户所有在线扩展连接。一个用户可同时挂多个平台的扩展。"""

    def __init__(self) -> None:
        self._conns: dict[int, list[Conn]] = {}
        # 收到扩展上报事件时的处理钩子（app 启动时注入真正的入库/处理；默认仅记日志）。
        # 签名 (user_id, org_id, event) -> None，已在调用方的 tenant 上下文内执行。
        self.on_event: Callable[[int, int | None, dict], None] | None = None

    def register(self, user_id: int, conn: Conn) -> None:
        self._conns.setdefault(user_id, []).append(conn)
        logger.info("扩展上线 user={} platform={}", user_id, conn.platform)

    def unregister(self, user_id: int, conn: Conn) -> None:
        conns = self._conns.get(user_id)
        if not conns:
            return
        if conn in conns:
            conns.remove(conn)
        if not conns:
            self._conns.pop(user_id, None)
        logger.info("扩展下线 user={} platform={}", user_id, conn.platform)

    def is_connected(self, user_id: int, platform: str | None = None) -> bool:
        conns = self._conns.get(user_id) or []
        if platform is None:
            return bool(conns)
        return any(c.platform == platform for c in conns)

    def platforms_of(self, user_id: int) -> list[str]:
        return sorted({c.platform for c in self._conns.get(user_id, [])})

    def connected_users(self) -> list[int]:
        return list(self._conns)

    async def send_to_user(
        self, user_id: int, command: dict, platform: str | None = None
    ) -> int:
        """把命令发给该用户的扩展（可按 platform 定向）。返回成功送达的连接数。

        发送失败的连接就地摘除（对端已断）。跨用户永不投递——路由键就是 user_id。
        """
        sent = 0
        for conn in list(self._conns.get(user_id, [])):
            if platform is not None and conn.platform != platform:
                continue
            try:
                await conn.ws.send_json(command)
                sent += 1
            except Exception:
                self.unregister(user_id, conn)
        if sent == 0:
            logger.warning("命令未送达 user={} platform={} type={}",
                           user_id, platform, command.get("type"))
        return sent


# 进程内单例：路由/状态查询共用同一张表。
hub = CrawlHub()

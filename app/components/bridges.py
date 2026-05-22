"""Shared BOSS / Qiancheng / Zhilian WebSocket bridge factories.

Both the homepage and the per-platform collection pages need a reference to the
same long-lived bridge instance — otherwise each module starts its own WS server
and binds the port twice. Defining the factories here lets every caller hit the
same `@st.cache_resource` entry.
"""
from __future__ import annotations

import streamlit as st
from loguru import logger

from recruitment_assistant.services.boss_ws_bridge import BossWSBridge
from recruitment_assistant.services.qiancheng_ws_bridge import QianchengWSBridge
from recruitment_assistant.services.zhilian_ws_bridge import ZhilianWSBridge
from recruitment_assistant.services.ws_server import BossWSServer, QianchengWSServer, ZhilianWSServer


@st.cache_resource
def _build_boss_bridge() -> BossWSBridge:
    server = BossWSServer()
    server.start()
    return BossWSBridge(server)


@st.cache_resource
def _build_qiancheng_bridge() -> QianchengWSBridge:
    server = QianchengWSServer()
    server.start()
    return QianchengWSBridge(server)


def get_boss_bridge() -> BossWSBridge:
    bridge = _build_boss_bridge()
    server = bridge.ws_server
    if not server.is_listening and server.startup_error:
        logger.warning("Boss WS 启动失败缓存命中，清除并重试：{}", server.startup_error)
        _build_boss_bridge.clear()
        bridge = _build_boss_bridge()
    return bridge


def get_qiancheng_bridge() -> QianchengWSBridge:
    bridge = _build_qiancheng_bridge()
    server = bridge.ws_server
    if not server.is_listening and server.startup_error:
        logger.warning("Qiancheng WS 启动失败缓存命中，清除并重试：{}", server.startup_error)
        _build_qiancheng_bridge.clear()
        bridge = _build_qiancheng_bridge()
    return bridge


@st.cache_resource
def _build_zhilian_bridge() -> ZhilianWSBridge:
    server = ZhilianWSServer()
    server.start()
    return ZhilianWSBridge(server)


def get_zhilian_bridge() -> ZhilianWSBridge:
    bridge = _build_zhilian_bridge()
    server = bridge.ws_server
    if not server.is_listening and server.startup_error:
        logger.warning("Zhilian WS 启动失败缓存命中，清除并重试：{}", server.startup_error)
        _build_zhilian_bridge.clear()
        bridge = _build_zhilian_bridge()
    return bridge

"""M2.5 采集中枢单元测试：按 user_id 路由 + 跨用户隔离 + 断连摘除。"""
import asyncio

from backend.app.crawl_hub import Conn, CrawlHub


class FakeWS:
    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("对端已断")
        self.sent.append(data)


def test_register_status_and_platforms():
    hub = CrawlHub()
    a = Conn(ws=FakeWS(), platform="boss")
    b = Conn(ws=FakeWS(), platform="qiancheng")
    hub.register(1, a)
    hub.register(1, b)

    assert hub.is_connected(1)
    assert hub.is_connected(1, "boss")
    assert not hub.is_connected(1, "zhilian")
    assert hub.platforms_of(1) == ["boss", "qiancheng"]
    assert hub.connected_users() == [1]

    hub.unregister(1, a)
    assert hub.platforms_of(1) == ["qiancheng"]
    hub.unregister(1, b)
    assert not hub.is_connected(1)
    assert hub.connected_users() == []


def test_send_routes_by_user_and_isolates():
    hub = CrawlHub()
    ws1 = FakeWS()
    ws2 = FakeWS()
    hub.register(1, Conn(ws=ws1, platform="boss"))
    hub.register(2, Conn(ws=ws2, platform="boss"))

    delivered = asyncio.run(hub.send_to_user(1, {"type": "start"}))
    assert delivered == 1
    assert ws1.sent == [{"type": "start"}]
    assert ws2.sent == []  # 跨用户永不投递


def test_send_platform_targeting():
    hub = CrawlHub()
    boss = FakeWS()
    qc = FakeWS()
    hub.register(1, Conn(ws=boss, platform="boss"))
    hub.register(1, Conn(ws=qc, platform="qiancheng"))

    asyncio.run(hub.send_to_user(1, {"type": "stop"}, platform="qiancheng"))
    assert qc.sent == [{"type": "stop"}]
    assert boss.sent == []


def test_send_to_absent_user_returns_zero():
    hub = CrawlHub()
    assert asyncio.run(hub.send_to_user(99, {"type": "x"})) == 0


def test_failed_send_evicts_connection():
    hub = CrawlHub()
    dead = Conn(ws=FakeWS(fail=True), platform="boss")
    hub.register(1, dead)
    assert asyncio.run(hub.send_to_user(1, {"type": "x"})) == 0
    assert not hub.is_connected(1)  # 发送失败即摘除

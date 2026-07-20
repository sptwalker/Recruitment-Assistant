"""_chat_completion 的按调用降级 + 不粘死 回归测试。

关键不变量：一次瞬时故障降级到备用后，不能永久把进程级缓存的服务实例钉在备用上；
主接口恢复后，下一次调用必须回到主接口。
"""
import sys
import types

import pytest


@pytest.fixture()
def stub_openai(monkeypatch):
    """注入假的 openai 模块。STATE['primary_fail'] 控制主接口连续失败次数。"""
    oa = types.ModuleType("openai")

    class APITimeoutError(Exception): ...
    class APIConnectionError(Exception): ...
    class RateLimitError(Exception): ...
    class APIStatusError(Exception):
        def __init__(self, msg, status_code=500):
            super().__init__(msg); self.status_code = status_code

    oa.APITimeoutError = APITimeoutError
    oa.APIConnectionError = APIConnectionError
    oa.RateLimitError = RateLimitError
    oa.APIStatusError = APIStatusError

    state = {"primary_fail": 0}

    class _Resp:
        def __init__(self, key): self.key = key

    class OpenAI:
        def __init__(self, api_key=None, base_url=None): self.api_key = api_key
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, model=None, **kw):
            if self.api_key == "primary":
                if state["primary_fail"] > 0:
                    state["primary_fail"] -= 1
                    raise APIConnectionError("primary blip")
                return _Resp("primary")
            return _Resp("backup")

    oa.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", oa)
    import recruitment_assistant.services.resume_ai_service as S
    monkeypatch.setattr(S._time, "sleep", lambda s: None, raising=True)  # 跳过退避等待
    S.pop_failover_notices()  # 清空可能的残留
    return S, state


def _svc(S):
    return S.ResumeAIService(endpoints=[
        {"name": "GLM", "api_key": "primary", "base_url": "u1", "model": "m1"},
        {"name": "Backup", "api_key": "backup", "base_url": "u2", "model": "m2"},
    ])


def test_response_format_unsupported_retries_without_it(monkeypatch):
    """端点不支持 response_format=json_object（400）→ 去掉该参数原地重试成功。"""
    oa = sys.modules.get("openai")
    import types
    oa = types.ModuleType("openai")

    class APITimeoutError(Exception): ...
    class APIConnectionError(Exception): ...
    class RateLimitError(Exception): ...
    class APIStatusError(Exception):
        def __init__(self, msg, status_code=500):
            super().__init__(msg); self.status_code = status_code
    oa.APITimeoutError = APITimeoutError
    oa.APIConnectionError = APIConnectionError
    oa.RateLimitError = RateLimitError
    oa.APIStatusError = APIStatusError

    seen = {"had_rf": [], "calls": 0}

    class _Resp:
        pass

    class OpenAI:
        def __init__(self, api_key=None, base_url=None): pass
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, model=None, **kw):
            seen["calls"] += 1
            has_rf = "response_format" in kw
            seen["had_rf"].append(has_rf)
            if has_rf:
                raise APIStatusError("invalid params, unknown response_format type 'json_object'", status_code=400)
            return _Resp()

    oa.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", oa)
    import recruitment_assistant.services.resume_ai_service as S
    monkeypatch.setattr(S._time, "sleep", lambda s: None, raising=True)

    svc = S.ResumeAIService(endpoints=[{"name": "GLM", "api_key": "k", "base_url": "u", "model": "m"}])
    resp = svc._chat_completion(messages=[], response_format={"type": "json_object"})
    assert isinstance(resp, _Resp)
    assert seen["had_rf"] == [True, False]   # 第一次带 rf 被拒，第二次去掉成功
    assert seen["calls"] == 2


def test_response_format_400_only_strips_that_param(monkeypatch):
    """非 response_format 的真 400 业务错误仍应直接抛出（不无脑吞）。"""
    import types
    oa = types.ModuleType("openai")

    class APITimeoutError(Exception): ...
    class APIConnectionError(Exception): ...
    class RateLimitError(Exception): ...
    class APIStatusError(Exception):
        def __init__(self, msg, status_code=500):
            super().__init__(msg); self.status_code = status_code
    oa.APITimeoutError = APITimeoutError
    oa.APIConnectionError = APIConnectionError
    oa.RateLimitError = RateLimitError
    oa.APIStatusError = APIStatusError

    class OpenAI:
        def __init__(self, api_key=None, base_url=None): pass
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, model=None, **kw):
            raise APIStatusError("context length exceeded", status_code=400)

    oa.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", oa)
    import recruitment_assistant.services.resume_ai_service as S
    import pytest
    svc = S.ResumeAIService(endpoints=[{"name": "GLM", "api_key": "k", "base_url": "u", "model": "m"}])
    with pytest.raises(Exception):
        svc._chat_completion(messages=[], response_format={"type": "json_object"})


def test_failover_to_backup_on_transient(stub_openai):
    S, state = stub_openai
    state["primary_fail"] = 3  # 用尽主接口本次调用的 3 次尝试
    svc = _svc(S)
    resp = svc._chat_completion(messages=[])
    assert resp.key == "backup"
    assert svc._idx == 0                      # 不得永久前移
    assert len(S.pop_failover_notices()) == 1


def test_returns_to_primary_after_recovery(stub_openai):
    S, state = stub_openai
    state["primary_fail"] = 3
    svc = _svc(S)
    assert svc._chat_completion(messages=[]).key == "backup"   # call1 降级
    S.pop_failover_notices()
    # 主接口已恢复（primary_fail 归零）
    r2 = svc._chat_completion(messages=[])
    assert r2.key == "primary"                # call2 回到主接口，不粘死
    assert len(S.pop_failover_notices()) == 0


def test_transient_retry_recovers_same_endpoint(stub_openai):
    S, state = stub_openai
    state["primary_fail"] = 1  # 仅 1 次失败 → 同端点重试即恢复，不降级
    svc = _svc(S)
    resp = svc._chat_completion(messages=[])
    assert resp.key == "primary"
    assert len(S.pop_failover_notices()) == 0


def test_all_endpoints_fail_raises(stub_openai):
    S, state = stub_openai
    state["primary_fail"] = 999
    svc = S.ResumeAIService(endpoints=[
        {"name": "GLM", "api_key": "primary", "base_url": "u1", "model": "m1"},
        {"name": "AlsoPrimary", "api_key": "primary", "base_url": "u2", "model": "m2"},
    ])
    with pytest.raises(Exception):
        svc._chat_completion(messages=[])

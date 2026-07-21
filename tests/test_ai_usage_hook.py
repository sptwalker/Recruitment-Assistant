"""_chat_completion 成功后写入 AI 用量记录（feature/endpoint/tokens）。"""
import sys
import types

from types import SimpleNamespace


def test_chat_completion_records_ai_usage(temp_resume_db, monkeypatch):
    # stub openai：create 返回带 usage 的响应
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
            return SimpleNamespace(usage=SimpleNamespace(
                prompt_tokens=30, completion_tokens=12, total_tokens=42))

    oa.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", oa)

    import recruitment_assistant.services.resume_ai_service as S
    from recruitment_assistant.services import monitoring
    from datetime import date

    svc = S.ResumeAIService(endpoints=[
        {"name": "GLM", "api_key": "k", "base_url": "u", "model": "GLM-5.2"},
    ])
    svc._chat_completion(feature="match", messages=[])

    rows = monitoring.list_ai_usage(date.today())
    assert len(rows) == 1
    assert rows[0]["功能模块"] == "岗位匹配"      # feature=match → label
    assert rows[0]["接口"] == "GLM" and rows[0]["total"] == 42
    assert rows[0]["降级"] == ""                  # idx 0 = 非降级

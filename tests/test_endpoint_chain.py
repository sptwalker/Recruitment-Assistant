"""get_endpoint_chain / 主备切换 / 解析专用接口 的降级链逻辑。"""
import pytest


@pytest.fixture()
def mgr(tmp_path, monkeypatch):
    import recruitment_assistant.config.ai_model_manager as M
    monkeypatch.setattr(M, "AI_MODELS_PATH", tmp_path / "ai_models.json", raising=True)
    # 屏蔽 .env 写入副作用
    monkeypatch.setattr(M, "_sync_env", lambda p: None, raising=True)
    monkeypatch.setattr(M, "_write_env", lambda u: None, raising=True)
    M.save_profiles({"active": "", "parse_profile_id": "", "profiles": [
        {"id": "slow", "name": "GLM", "api_key": "k1", "base_url": "u1",
         "model": "GLM-5.2", "enabled": True, "primary": True},
        {"id": "fast", "name": "DeepSeek", "api_key": "k2", "base_url": "u2",
         "model": "deepseek-chat", "enabled": True, "primary": False},
        {"id": "nokey", "name": "空Key", "api_key": "", "base_url": "u3",
         "model": "m3", "enabled": True, "primary": False},
    ]})
    return M


def test_match_chain_primary_first_and_excludes_keyless(mgr):
    names = [e["name"] for e in mgr.get_endpoint_chain("match")]
    assert names == ["GLM", "DeepSeek"]  # 主接口在前；无 key 的被排除


def test_parse_follows_primary_when_unset(mgr):
    assert [e["name"] for e in mgr.get_endpoint_chain("parse")][0] == "GLM"


def test_parse_uses_dedicated_endpoint(mgr):
    mgr.set_parse_profile("fast")
    parse = [e["name"] for e in mgr.get_endpoint_chain("parse")]
    match = [e["name"] for e in mgr.get_endpoint_chain("match")]
    assert parse[0] == "DeepSeek"     # 解析走快模型
    assert "GLM" in parse             # 主接口仍作降级兜底
    assert match[0] == "GLM"          # 匹配不受影响


def test_set_primary_is_single_select(mgr):
    mgr.set_primary_profile("fast")
    data = mgr.load_profiles()
    primaries = [p["id"] for p in data["profiles"] if p.get("primary")]
    assert primaries == ["fast"]


def test_disable_primary_hands_off(mgr):
    mgr.set_profile_enabled("slow", False)
    data = mgr.load_profiles()
    prim = [p["name"] for p in data["profiles"] if p.get("primary")]
    assert prim == ["DeepSeek"]       # 禁用主接口后让位给下一个启用项


def test_disabled_parse_endpoint_falls_back(mgr):
    mgr.set_parse_profile("fast")
    mgr.set_profile_enabled("fast", False)
    assert [e["name"] for e in mgr.get_endpoint_chain("parse")][0] == "GLM"

"""多 AI 接口 profile 管理：增删改查 + 主要/启用开关 + 自动降级链 + 同步 .env。

数据存储于 data/ai_models.json，首次启动自动从 .env 迁移。
- 每个 profile 有 enabled(是否启用) / primary(是否主接口，单选) 两个开关。
- 主接口同步进 .env 的 AI_API_KEY / AI_BASE_URL / AI_MODEL，保证脚本 / settings.py 等
  未升级路径仍可用。
- get_endpoint_chain() 返回 [主接口, ...其余已启用] 有序列表，供服务层做失败降级。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from recruitment_assistant.config.settings import get_settings

AI_MODELS_PATH = Path("data/ai_models.json")

PRESETS: dict[str, tuple[str, str]] = {
    "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "MiniMax": ("https://api.minimaxi.com/v1", "MiniMax-Text-01"),
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _read_env() -> dict[str, str]:
    env_path = Path(".env")
    result: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_env(updates: dict[str, str]) -> None:
    env_path = Path(".env")
    existing = _read_env()
    existing.update(updates)
    env_path.write_text(
        "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()


def _sync_env(profile: dict) -> None:
    _write_env({
        "AI_API_KEY": profile.get("api_key", ""),
        "AI_BASE_URL": profile.get("base_url", ""),
        "AI_MODEL": profile.get("model", ""),
    })


def _detect_name(base_url: str) -> str:
    url = base_url.lower()
    if "deepseek" in url:
        return "DeepSeek"
    if "dashscope" in url:
        return "通义千问"
    if "minimax" in url:
        return "MiniMax"
    if "openai" in url:
        return "OpenAI"
    return "自定义模型"


def _migrate_from_env() -> dict:
    env = _read_env()
    api_key = env.get("AI_API_KEY", "")
    base_url = env.get("AI_BASE_URL", "https://api.deepseek.com/v1")
    model = env.get("AI_MODEL", "deepseek-chat")
    pid = _new_id()
    profile = {
        "id": pid,
        "name": _detect_name(base_url),
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "enabled": True,
        "primary": True,
    }
    data = {"active": pid, "profiles": [profile]}
    save_profiles(data)
    return data


def _normalize(data: dict) -> dict:
    """幂等补齐 enabled/primary 字段（老库迁移），并保证恰有一个 primary。

    - 旧 profile 无 enabled → 默认 True。
    - 旧 profile 无 primary → 由旧 active 那条置 True。
    - 若出现零个或多个 primary，收敛为唯一：优先旧 active，否则第一个启用项。
    """
    profiles = data.get("profiles", [])
    if not profiles:
        return data

    active_id = data.get("active")
    changed = False
    for p in profiles:
        if "enabled" not in p:
            p["enabled"] = True
            changed = True
        if "primary" not in p:
            p["primary"] = (p["id"] == active_id)
            changed = True

    primaries = [p for p in profiles if p.get("primary")]
    if len(primaries) != 1:
        chosen = next((p for p in profiles if p["id"] == active_id), None) \
            or next((p for p in profiles if p.get("enabled")), None) \
            or profiles[0]
        for p in profiles:
            p["primary"] = (p is chosen)
        chosen["enabled"] = True
        changed = True

    primary = next(p for p in profiles if p.get("primary"))
    if data.get("active") != primary["id"]:
        data["active"] = primary["id"]
        changed = True

    if changed:
        save_profiles(data)
    return data


def load_profiles() -> dict:
    if AI_MODELS_PATH.exists():
        try:
            data = json.loads(AI_MODELS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "profiles" in data:
                return _normalize(data)
        except (json.JSONDecodeError, OSError):
            pass
    return _migrate_from_env()


def save_profiles(data: dict) -> None:
    AI_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AI_MODELS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_primary_profile() -> dict | None:
    data = load_profiles()
    for p in data.get("profiles", []):
        if p.get("primary"):
            return p
    profiles = data.get("profiles", [])
    return profiles[0] if profiles else None


def get_endpoint_chain(purpose: str = "match") -> list[dict]:
    """返回失败降级链：[首选接口, ...其余已启用接口]，均要求 api_key 非空。

    purpose="match"（默认）：首选=主接口（primary）。
    purpose="parse"：首选=解析专用接口（parse_profile_id，若设置且启用且有 key）；
      未设置或不可用时回退为主接口。用于简历解析可选一个更快的模型。
    其余已启用接口按列表顺序作为降级备用。
    """
    data = load_profiles()
    profiles = data.get("profiles", [])
    primary = next((p for p in profiles if p.get("primary")), None)

    head = primary
    if purpose == "parse":
        pid = data.get("parse_profile_id")
        if pid:
            cand = next((p for p in profiles if p["id"] == pid), None)
            if cand and cand.get("enabled") and cand.get("api_key"):
                head = cand

    chain: list[dict] = []
    if head and head.get("api_key"):
        chain.append(head)
    for p in profiles:
        if p is head:
            continue
        if p.get("enabled") and p.get("api_key"):
            chain.append(p)
    return chain


def set_parse_profile(profile_id: str | None) -> None:
    """设置简历解析专用接口；传 None 或空表示跟随主接口。"""
    data = load_profiles()
    if profile_id and not any(p["id"] == profile_id for p in data["profiles"]):
        raise ValueError(f"Profile {profile_id} not found")
    data["parse_profile_id"] = profile_id or ""
    save_profiles(data)


def set_primary_profile(profile_id: str) -> None:
    """设为主接口（单选）：清其他 primary，本条 primary+enabled，同步 .env。"""
    data = load_profiles()
    target = None
    for p in data["profiles"]:
        if p["id"] == profile_id:
            target = p
        p["primary"] = (p["id"] == profile_id)
    if target is None:
        raise ValueError(f"Profile {profile_id} not found")
    target["enabled"] = True
    data["active"] = profile_id
    save_profiles(data)
    _sync_env(target)


def set_profile_enabled(profile_id: str, enabled: bool) -> None:
    """启用/禁用某接口。禁用主接口时清除 primary 并把 .env 让给下一个可用启用项。"""
    data = load_profiles()
    target = next((p for p in data["profiles"] if p["id"] == profile_id), None)
    if target is None:
        raise ValueError(f"Profile {profile_id} not found")
    target["enabled"] = enabled
    if not enabled and target.get("primary"):
        target["primary"] = False
        fallback = next(
            (p for p in data["profiles"] if p["id"] != profile_id and p.get("enabled")),
            None,
        )
        if fallback:
            fallback["primary"] = True
            data["active"] = fallback["id"]
            _sync_env(fallback)
        else:
            data["active"] = ""
            _write_env({"AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""})
    save_profiles(data)


def add_profile(name: str, api_key: str, base_url: str, model: str) -> dict:
    data = load_profiles()
    is_first = len(data["profiles"]) == 0
    profile = {
        "id": _new_id(),
        "name": name,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "enabled": True,
        "primary": is_first,
    }
    data["profiles"].append(profile)
    if is_first:
        data["active"] = profile["id"]
        _sync_env(profile)
    save_profiles(data)
    return profile


def update_profile(profile_id: str, **kwargs: str) -> None:
    data = load_profiles()
    for p in data["profiles"]:
        if p["id"] == profile_id:
            for k, v in kwargs.items():
                if k in ("name", "api_key", "base_url", "model"):
                    p[k] = v
            save_profiles(data)
            if p.get("primary"):
                _sync_env(p)
            return
    raise ValueError(f"Profile {profile_id} not found")


def delete_profile(profile_id: str) -> None:
    data = load_profiles()
    was_primary = any(p["id"] == profile_id and p.get("primary") for p in data["profiles"])
    data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
    if was_primary:
        # 主接口被删：primary 让给第一个启用项（无启用项则第一个），否则清空 .env
        fallback = next((p for p in data["profiles"] if p.get("enabled")), None) \
            or (data["profiles"][0] if data["profiles"] else None)
        if fallback:
            fallback["primary"] = True
            fallback["enabled"] = True
            data["active"] = fallback["id"]
            _sync_env(fallback)
        else:
            data["active"] = ""
            _write_env({"AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""})
    save_profiles(data)

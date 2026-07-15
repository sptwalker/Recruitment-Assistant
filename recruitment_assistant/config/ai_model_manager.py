"""多 AI 模型 profile 管理：增删改查 + 切换 active profile + 同步 .env。

数据存储于 data/ai_models.json，首次启动自动从 .env 迁移。
切换 profile 时同步更新 .env 中的 AI_API_KEY / AI_BASE_URL / AI_MODEL，
确保下游 settings.py / ResumeAIService 无需改动。
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
    }
    data = {"active": pid, "profiles": [profile]}
    save_profiles(data)
    return data


def load_profiles() -> dict:
    if AI_MODELS_PATH.exists():
        try:
            data = json.loads(AI_MODELS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "profiles" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return _migrate_from_env()


def save_profiles(data: dict) -> None:
    AI_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AI_MODELS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_active_profile() -> dict | None:
    data = load_profiles()
    active_id = data.get("active")
    for p in data.get("profiles", []):
        if p["id"] == active_id:
            return p
    profiles = data.get("profiles", [])
    return profiles[0] if profiles else None


def set_active_profile(profile_id: str) -> None:
    data = load_profiles()
    for p in data["profiles"]:
        if p["id"] == profile_id:
            data["active"] = profile_id
            save_profiles(data)
            _sync_env(p)
            return
    raise ValueError(f"Profile {profile_id} not found")


def add_profile(name: str, api_key: str, base_url: str, model: str) -> dict:
    data = load_profiles()
    profile = {
        "id": _new_id(),
        "name": name,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }
    data["profiles"].append(profile)
    if len(data["profiles"]) == 1:
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
            if data.get("active") == profile_id:
                _sync_env(p)
            return
    raise ValueError(f"Profile {profile_id} not found")


def delete_profile(profile_id: str) -> None:
    data = load_profiles()
    data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
    if data.get("active") == profile_id:
        if data["profiles"]:
            data["active"] = data["profiles"][0]["id"]
            _sync_env(data["profiles"][0])
        else:
            data["active"] = ""
            _write_env({"AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""})
    save_profiles(data)

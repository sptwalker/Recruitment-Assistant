from recruitment_assistant.services.resume_ai_service import (
    PLATFORM_VALID,
    normalize_platform,
)


def test_boss_aliases_map_to_canonical():
    assert normalize_platform("BOSS") == "BOSS直聘"
    assert normalize_platform("boss") == "BOSS直聘"
    assert normalize_platform("Boss") == "BOSS直聘"


def test_canonical_passes_through():
    for v in PLATFORM_VALID:
        assert normalize_platform(v) == v


def test_unknown_returns_original():
    assert normalize_platform("拉勾") == "拉勾"


def test_none_and_empty():
    assert normalize_platform(None) is None
    assert normalize_platform("") == ""


def test_strips_whitespace():
    assert normalize_platform("  BOSS  ") == "BOSS直聘"

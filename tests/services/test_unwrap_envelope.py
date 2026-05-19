"""验证 _unwrap_candidate_envelope 兜底逻辑。

AI 偶尔会把候选人字段嵌进一层包装（如 {"candidates": {"name": ...}}），
service 层应该自动解开成平铺结构以匹配 CandidateCreate。
"""

from recruitment_assistant.services.resume_ai_service import (
    _unwrap_candidate_envelope,
)


def test_already_flat_passes_through():
    data = {"name": "张三", "age": 28}
    assert _unwrap_candidate_envelope(data) == data


def test_unwraps_candidates_envelope():
    data = {"candidates": {"name": "张三", "age": 28}, "honors": [{"honor_name": "X"}]}
    result = _unwrap_candidate_envelope(data)
    assert result["name"] == "张三"
    assert result["age"] == 28
    assert "candidates" not in result
    # 外层并列字段应保留
    assert result["honors"] == [{"honor_name": "X"}]


def test_unwraps_candidate_singular_envelope():
    data = {"candidate": {"name": "李四"}}
    assert _unwrap_candidate_envelope(data) == {"name": "李四"}


def test_unwraps_data_envelope():
    data = {"data": {"name": "王五"}}
    assert _unwrap_candidate_envelope(data) == {"name": "王五"}


def test_inner_overrides_outer_on_conflict():
    """若内外层都写了同一字段，以内层（候选人对象本身）为准。"""
    data = {"name": "外层不该有", "candidates": {"name": "内层正确"}}
    # name 在外层就直接 pass through，不走 unwrap 路径——这是 well-formed 的快路径
    result = _unwrap_candidate_envelope(data)
    assert result["name"] == "外层不该有"


def test_no_envelope_no_name_returns_original():
    """没有任何包装也没有 name 字段 → 原样返回（让 pydantic 去报错）。"""
    data = {"phone": "13800000000", "age": 30}
    assert _unwrap_candidate_envelope(data) == data


def test_non_dict_input_passes_through():
    assert _unwrap_candidate_envelope([1, 2, 3]) == [1, 2, 3]
    assert _unwrap_candidate_envelope(None) is None
    assert _unwrap_candidate_envelope("string") == "string"

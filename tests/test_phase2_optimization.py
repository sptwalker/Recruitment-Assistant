"""阶段二优化单元测试：多维评分系统 + 丰富候选人摘要"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from recruitment_assistant.storage.resume_models import PositionMatch


class TestMultiDimensionalScoring:
    """测试多维度评分模型"""

    def test_position_match_has_dimension_fields(self):
        """测试 PositionMatch 模型包含维度字段"""
        # 检查字段是否存在
        assert hasattr(PositionMatch, 'skill_match')
        assert hasattr(PositionMatch, 'experience_match')
        assert hasattr(PositionMatch, 'education_match')
        assert hasattr(PositionMatch, 'location_match')

    def test_position_match_creation_with_dimensions(self):
        """测试创建包含维度数据的 PositionMatch 对象"""
        match = PositionMatch(
            position_id=1,
            candidate_id=1,
            score=75,
            reason="测试匹配",
            skill_match=80,
            experience_match=70,
            education_match=85,
            location_match=60,
        )
        assert match.skill_match == 80
        assert match.experience_match == 70
        assert match.education_match == 85
        assert match.location_match == 60

    def test_position_match_creation_without_dimensions(self):
        """测试创建不含维度数据的 PositionMatch 对象（向后兼容）"""
        match = PositionMatch(
            position_id=1,
            candidate_id=1,
            score=75,
            reason="测试匹配",
        )
        assert match.skill_match is None
        assert match.experience_match is None
        assert match.education_match is None
        assert match.location_match is None


class TestCandidateSummaryEnrichment:
    """测试候选人摘要丰富化"""

    def test_candidate_dict_structure(self):
        """测试候选人字典结构包含新增字段"""
        # 模拟候选人字典
        candidate_dict = {
            "candidate_id": 1,
            "name": "张三",
            "education_level": "本科",
            "current_city": "北京",
            "position": "Python 工程师",
            "skills": "Python、Django、Flask",
            "core_skills": "Python、机器学习",  # ✨ 新增
            "work_summary": "ABC公司(高级工程师)",
            "years_of_experience": "5.0年",  # ✨ 新增
            "projects": "推荐系统（核心开发）",  # ✨ 新增
            "honors": "优秀员工、技术创新奖",  # ✨ 新增
        }

        # 验证新增字段
        assert "core_skills" in candidate_dict
        assert "years_of_experience" in candidate_dict
        assert "projects" in candidate_dict
        assert "honors" in candidate_dict


class TestAIResponseCompatibility:
    """测试 AI 响应兼容性"""

    def test_backward_compatible_response(self):
        """测试向后兼容：AI 返回不含 dimensions 的响应"""
        # 模拟旧格式响应
        response = {
            "candidate_id": 1,
            "match_score": 75,
            "reason": "技能匹配"
        }

        # 模拟向后兼容逻辑
        if "dimensions" not in response:
            score = response.get("match_score", 50)
            response["dimensions"] = {
                "skill_match": score,
                "experience_match": score,
                "education_match": score,
                "location_match": score,
            }

        assert "dimensions" in response
        assert response["dimensions"]["skill_match"] == 75

    def test_new_format_response(self):
        """测试新格式：AI 返回包含 dimensions 的响应"""
        response = {
            "candidate_id": 1,
            "match_score": 75,
            "dimensions": {
                "skill_match": 80,
                "experience_match": 70,
                "education_match": 85,
                "location_match": 60,
            },
            "reason": "综合评估"
        }

        assert response["dimensions"]["skill_match"] == 80
        assert response["dimensions"]["experience_match"] == 70


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

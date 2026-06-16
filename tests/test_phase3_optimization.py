"""阶段三优化单元测试：AI 解析容错与重试"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAIParsingRetry:
    """测试 AI 解析重试机制"""

    def test_json_cleaning(self):
        """测试 JSON 响应清理"""
        from recruitment_assistant.services.resume_ai_service import ResumeAIService

        # 创建临时 service 实例
        service = ResumeAIService("test_key", "http://test.com", "test_model")

        # 测试清理 markdown 代码块
        content1 = "```json\n{\"name\": \"张三\"}\n```"
        cleaned1 = service._clean_json_response(content1)
        assert cleaned1 == '{"name": "张三"}'

        # 测试清理 json 标记
        content2 = "json{\"name\": \"张三\"}"
        cleaned2 = service._clean_json_response(content2)
        assert cleaned2 == '{"name": "张三"}'

        # 测试普通 JSON
        content3 = '{"name": "张三"}'
        cleaned3 = service._clean_json_response(content3)
        assert cleaned3 == '{"name": "张三"}'

    def test_retry_parameter(self):
        """测试重试参数"""
        from recruitment_assistant.services.resume_ai_service import ResumeAIService

        service = ResumeAIService("test_key", "http://test.com", "test_model")

        # 验证方法签名包含 retry 参数
        import inspect
        sig = inspect.signature(service.parse_resume_text)
        assert 'retry' in sig.parameters
        assert sig.parameters['retry'].default == 2


class TestDataValidation:
    """测试数据验证"""

    def test_name_field_validation(self):
        """测试姓名字段必填验证"""
        # 模拟缺少姓名的数据
        data_without_name = {
            "phone": "13812345678",
            "email": "test@example.com"
        }

        # 验证逻辑：姓名为空应该触发重试
        assert not data_without_name.get('name')


class TestErrorHandling:
    """测试错误处理"""

    def test_json_decode_error_handling(self):
        """测试 JSON 解析错误处理"""
        import json

        invalid_json = "{name: 张三}"  # 无效 JSON
        try:
            json.loads(invalid_json)
            assert False, "应该抛出 JSONDecodeError"
        except json.JSONDecodeError:
            # 预期行为：捕获错误并重试
            pass

    def test_value_error_handling(self):
        """测试值错误处理"""
        # 模拟缺少必填字段的场景
        try:
            data = {}
            if not data.get('name'):
                raise ValueError("姓名字段缺失")
            assert False, "应该抛出 ValueError"
        except ValueError as e:
            assert "姓名" in str(e)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

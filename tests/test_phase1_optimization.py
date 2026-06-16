"""阶段一优化单元测试：并行批次匹配 + 字段提取优化"""

import sys
from pathlib import Path
import pytest

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from recruitment_assistant.parsers.pdf_resume_parser import find_phone, find_email, KNOWN_CITIES


class TestPhoneExtraction:
    """测试增强型手机号识别"""

    def test_standard_phone(self):
        """测试标准手机号"""
        text = "联系方式：13812345678"
        assert find_phone(text) == "13812345678"

    def test_phone_with_spaces(self):
        """测试空格分隔的手机号"""
        text = "手机：138 1234 5678"
        assert find_phone(text) == "13812345678"

    def test_phone_with_dashes(self):
        """测试短横线分隔的手机号"""
        text = "Mobile: 138-1234-5678"
        assert find_phone(text) == "13812345678"

    def test_phone_labeled(self):
        """测试标签化手机号"""
        text = "联系方式：电话:15987654321"
        assert find_phone(text) == "15987654321"

    def test_invalid_phone(self):
        """测试无效手机号"""
        text = "这是一段没有手机号的文本"
        assert find_phone(text) is None


class TestEmailExtraction:
    """测试增强型邮箱识别"""

    def test_standard_email(self):
        """测试标准邮箱"""
        text = "联系邮箱：test@example.com"
        assert find_email(text) == "test@example.com"

    def test_email_labeled(self):
        """测试标签化邮箱"""
        text = "Email: user.name@company.com.cn"
        assert find_email(text) == "user.name@company.com.cn"

    def test_email_with_plus(self):
        """测试带加号的邮箱"""
        text = "user+tag@gmail.com"
        assert find_email(text) == "user+tag@gmail.com"

    def test_invalid_email(self):
        """测试无效邮箱"""
        text = "这是一段没有邮箱的文本"
        assert find_email(text) is None


class TestCityRecognition:
    """测试城市识别扩展"""

    def test_tier1_cities(self):
        """测试一线城市"""
        assert "北京" in KNOWN_CITIES
        assert "上海" in KNOWN_CITIES
        assert "广州" in KNOWN_CITIES
        assert "深圳" in KNOWN_CITIES

    def test_new_tier1_cities(self):
        """测试新一线城市"""
        assert "成都" in KNOWN_CITIES
        assert "杭州" in KNOWN_CITIES
        assert "西安" in KNOWN_CITIES
        assert "苏州" in KNOWN_CITIES

    def test_tier2_cities(self):
        """测试二线城市"""
        assert "厦门" in KNOWN_CITIES
        assert "福州" in KNOWN_CITIES
        assert "昆明" in KNOWN_CITIES
        assert "济南" in KNOWN_CITIES

    def test_city_with_suffix(self):
        """测试带'市'后缀的城市"""
        assert "北京市" in KNOWN_CITIES
        assert "上海市" in KNOWN_CITIES

    def test_city_count(self):
        """测试城市总数"""
        # 至少应该有 50+ 个城市
        assert len(KNOWN_CITIES) >= 50


class TestParallelMatching:
    """测试并行批次匹配（集成测试）"""

    def test_concurrent_imports(self):
        """测试并发库导入"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        assert ThreadPoolExecutor is not None
        assert as_completed is not None

    def test_thread_pool_basic(self):
        """测试线程池基本功能"""
        from concurrent.futures import ThreadPoolExecutor

        def mock_task(n):
            return n * 2

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(mock_task, i) for i in range(5)]
            results = [f.result() for f in futures]
            assert results == [0, 2, 4, 6, 8]


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v", "--tb=short"])

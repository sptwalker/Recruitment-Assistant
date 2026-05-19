"""AI 简历结构化解析服务。

使用 OpenAI 兼容 API（DeepSeek / 通义千问 / OpenAI）将纯文本简历
结构化为标准字段，写入 SQLite resume_archive 数据库。
"""

from __future__ import annotations

import json
import re

from loguru import logger

from recruitment_assistant.schemas.resume_archive import CandidateCreate


def _normalize_base_url(base_url: str) -> str:
    """规范化 base_url：去尾部斜杠 + 修常见错误 path（如 /v4 → /v1，缺路径补 /v1）。"""
    if not base_url:
        return base_url
    url = base_url.strip().rstrip("/")
    # DeepSeek 只有 /v1
    if "deepseek.com" in url:
        # 错误版本号 /v2 /v3 /v4 ...
        if re.search(r"/v[2-9]\d*$", url):
            fixed = re.sub(r"/v[2-9]\d*$", "/v1", url)
            logger.warning("DeepSeek base_url 已自动修正：{} → {}", url, fixed)
            return fixed
        # 缺路径：https://api.deepseek.com → https://api.deepseek.com/v1
        if not re.search(r"/v\d+$", url):
            fixed = url + "/v1"
            logger.warning("DeepSeek base_url 缺路径，已自动补全：{} → {}", url, fixed)
            return fixed
    # OpenAI 没有版本号 / 通义千问保留路径
    return url


SYSTEM_PROMPT = """你是一个专业的简历解析助手。请将以下简历纯文本提取为标准 JSON 格式。

输出 JSON 结构（所有字段可选，缺失则为 null）：
{
  "name": "姓名",
  "gender": "性别(男/女)",
  "age": 数字或null,
  "birth_date": "YYYY-MM-DD或null",
  "phone": "手机号",
  "email": "邮箱",
  "wechat": "微信号",
  "current_city": "现居城市",
  "education_level": "最高学历(大专/本科/硕士/博士)",
  "self_intro": "自我评价摘要(50字内)",
  "educations": [{"school_name":"","education_level":"","major":"","degree":"","start_date":"","end_date":""}],
  "work_experiences": [{"company_name":"","position":"","industry":"","start_date":"","end_date":"","job_content":""}],
  "project_experiences": [{"project_name":"","project_role":"","project_desc":"","project_result":""}],
  "skills": [{"skill_type":"专业/语言/工具/证书","skill_name":"","proficiency":"精通/熟练/了解"}],
  "job_intention": {"target_position":"","target_city":"","expected_salary":"","job_status":""},
  "honors": [{"honor_name":"","honor_level":""}]
}

要求：
1. 只输出 JSON，不要任何解释文字
2. 日期格式 YYYY-MM-DD，缺失年份用 null
3. 手机号只保留 11 位数字
4. 学历统一为：大专/本科/硕士/博士/高中/中专
5. 如果简历内容不完整，尽量提取已有信息"""


class ResumeAIService:
    """AI 简历结构化解析 + 岗位匹配服务。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = _normalize_base_url(base_url)
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def parse_resume_text(self, raw_text: str) -> CandidateCreate | None:
        """调 LLM 将纯文本简历结构化为 CandidateCreate。"""
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置，请在 .env 文件中设置 AI_API_KEY")
        if not raw_text or len(raw_text.strip()) < 20:
            return None
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"请解析以下简历：\n\n{raw_text[:8000]}"},
                ],
                temperature=0.1,
                timeout=60,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content.strip()
            # 去掉可能的 markdown 代码块标记
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            data = json.loads(content)
            return CandidateCreate(**data)
        except json.JSONDecodeError as exc:
            logger.warning("AI 返回内容非合法 JSON：{}", exc)
            return None
        except Exception as exc:
            logger.error("AI 解析简历失败：{}", exc)
            raise

    def match_candidates(
        self, position_requirements: str, candidates: list[dict], top_n: int = 10
    ) -> list[dict]:
        """AI 匹配岗位需求与候选人列表，返回排序结果。"""
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置")
        candidates_text = "\n".join(
            f"ID={c.get('candidate_id')} 姓名={c.get('name')} 学历={c.get('education_level')} "
            f"城市={c.get('current_city')} 岗位={c.get('position', '')}"
            for c in candidates[:50]
        )
        prompt = (
            f"岗位要求：\n{position_requirements}\n\n"
            f"候选人列表：\n{candidates_text}\n\n"
            f"请从中选出最匹配的 {top_n} 位候选人，按匹配度从高到低排序。\n"
            f"输出 JSON 数组：[{{\"candidate_id\": ID, \"match_score\": 0-100, \"reason\": \"匹配原因\"}}]"
        )
        try:
            # 注意：这里返回的是 JSON 数组，不能用 json_object 模式（json_object 只支持对象）
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是招聘匹配助手，只输出 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                timeout=60,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except Exception as exc:
            logger.error("AI 岗位匹配失败：{}", exc)
            return []

    def generate_interview_outline(self, candidate_info: str, position: str) -> str:
        """AI 生成面试大纲。"""
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置")
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是资深 HR 面试官，请生成结构化面试大纲。"},
                    {"role": "user", "content": f"候选人信息：\n{candidate_info}\n\n应聘岗位：{position}\n\n请生成 5-8 个面试问题，分为专业能力、项目经验、软技能三个维度。"},
                ],
                temperature=0.3,
                timeout=60,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("AI 生成面试大纲失败：{}", exc)
            return f"生成失败：{exc}"

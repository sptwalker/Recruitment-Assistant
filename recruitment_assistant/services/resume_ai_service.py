"""AI 简历结构化解析服务。

使用 OpenAI 兼容 API（DeepSeek / 通义千问 / OpenAI）将纯文本简历
结构化为标准字段，写入 SQLite resume_archive 数据库。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger

from recruitment_assistant.schemas.resume_archive import CandidateCreate


MAX_RESUME_TEXT_CHARS = 25000
"""单份简历送入 AI 的最大字符数。

DeepSeek context 64k，单份 PDF 简历 95% 在 12k 字内；25k 阈值覆盖长简历，
配合 system prompt + 输出预算 ~6k 总占用约 32k，留足 token 余量。
"""


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


PLATFORM_ALIAS = {
    "BOSS": "BOSS直聘",
    "boss": "BOSS直聘",
    "Boss": "BOSS直聘",
    "BOSS直聘": "BOSS直聘",
    "智联": "智联招聘",
    "智联招聘": "智联招聘",
    "51": "51前程无忧",
    "51job": "51前程无忧",
    "前程无忧": "51前程无忧",
    "51前程无忧": "51前程无忧",
}
PLATFORM_VALID = {"BOSS直聘", "智联招聘", "51前程无忧"}


def normalize_platform(name: str | None) -> str | None:
    """把 source_platform 规范成 3 个枚举值之一，未知值返回原值。"""
    if not name:
        return name
    return PLATFORM_ALIAS.get(name.strip(), name)


# AI 偶尔会把候选人字段嵌进 candidates / candidate / data 包装层（早期 prompt 用 "candidates 主信息" 标题诱导出来的）。
# CandidateCreate 期望平铺，遇到这种结构自动解包。
_CANDIDATE_ENVELOPE_KEYS = ("candidates", "candidate", "data", "result")


def _unwrap_candidate_envelope(data: dict) -> dict:
    """如果 AI 把候选人字段包进了一层包装，自动解开成平铺 dict。"""
    if not isinstance(data, dict):
        return data
    # 当 name 缺失但发现包装 key 下面挂了 dict，把包装层解开（合并外层数组字段）
    if "name" in data:
        return data
    for key in _CANDIDATE_ENVELOPE_KEYS:
        inner = data.get(key)
        if isinstance(inner, dict) and "name" in inner:
            # 合并：内层覆盖外层（外层可能有 honors/educations 等并列项）
            merged = {**data, **inner}
            merged.pop(key, None)
            logger.warning("AI 输出嵌套了 {} 包装层，已自动解包", key)
            return merged
    return data


def _guess_name_from_source(source_name: str | None) -> str | None:
    """从附件文件名中兜底提取候选人姓名，如：李晓博-32岁-本科-BOSS直聘-xxx.pdf。"""
    if not source_name:
        return None
    name = source_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = re.sub(r"\.(pdf|docx?|PDF|DOCX?)$", "", name).strip()
    for sep in ("-", "_", " ", "（", "("):
        if sep in name:
            name = name.split(sep, 1)[0].strip()
            break
    if not name or name.lower() in {"resume", "cv", "简历"}:
        return None
    if re.search(r"[\u4e00-\u9fffA-Za-z]", name):
        return name[:32]
    return None


def _ensure_candidate_name(data: dict, source_name: str | None = None) -> dict:
    """确保 CandidateCreate 必填 name 为有效字符串，避免 AI 返回 null 造成整份简历失败。"""
    if not isinstance(data, dict):
        return data
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        data["name"] = name.strip()
        return data
    fallback_name = _guess_name_from_source(source_name) or "未知候选人"
    data["name"] = fallback_name
    logger.warning("AI 未返回有效姓名，已使用兜底姓名：{}", fallback_name)
    return data



_SYSTEM_PROMPT_TEMPLATE = """你是一个专业的简历解析助手。把下列简历纯文本结构化为标准 JSON 对象。

# 输出结构

JSON 顶层就是一个候选人对象，**不要**再嵌套 `candidates` 之类的外层包装。
顶层字段名固定为下列 16 个之一：
  name, gender, age, birth_date, phone, email, wechat, current_city,
  education_level, self_intro, educations, work_experiences,
  project_experiences, skills, job_intention, honors

例：
```
{
  "name": "张三",
  "age": 28,
  "phone": "13800000000",
  ...,
  "educations": [...],
  "work_experiences": [...]
}
```

不要写成 `{"candidates": {"name": ..., ...}}` —— 那是错的。

# 顶层字段表

## 候选人主信息（直接放在顶层，不要嵌套）
- name (str, 必填)：姓名。中文名/英文名/单字名都要识别。name 绝不能返回 null；如果简历正文没有姓名，优先从附件文件名/文本开头提取；仍无法识别时返回 "未知候选人"。
- gender (str)：性别，只能是 "男" / "女"。从姓名/称谓/简历头部识别。
- age (int)：年龄。识别优先级：(1) 简历明确写"XX岁"取值 (2) 只写出生日期 → 用 {current_year} 减去出生年得到 (3) 简历只写工作年限或毕业年 → 不要硬猜，留 null。
- birth_date (str|null)：出生日期 YYYY-MM-DD，可只到年/月。
- phone (str)：手机号。只保留 11 位数字，去掉所有空格、横线、括号。
- email (str)：邮箱。
- wechat (str)：微信号 / WX。
- current_city (str)：现居城市。注意区别于"籍贯/家乡"——只取候选人当前生活/工作所在地。
- education_level (str)：最高学历层次。只能是 "高中" / "中专" / "大专" / "本科" / "硕士" / "博士" 之一。
- self_intro (str)：自我评价摘要，控制在 80 字内。

## educations[] 教育经历
- school_name (str)：学校名。完整官方名（如"湖南大学"而非"湖大"）。
- education_level (str)：本段学历，枚举同上。
- major (str)：专业。
- degree (str)：具体学位（"工学学士" / "管理学硕士" / "MBA"）。注意：和 education_level 不同——education_level 是层次，degree 是学位名称，简历常省略 degree，留 null 不要硬填。
- start_date / end_date (str|null)：YYYY-MM-DD，缺月份补 01；在读用 null 表示。
- is_full_time (int)：1=全日制，0=非全日制（在职/函授/网教）。默认 1。

## work_experiences[] 工作经历
- company_name (str)：公司名。
- industry (str)：行业（如"互联网"、"制造业"、"金融"）。简历明确写出才填。
- position (str)：职位名（如"高级工程师"）。
- start_date / end_date (str|null)：YYYY-MM-DD，至今/在职 → null。
- job_content (str)：工作内容描述。

## project_experiences[] 项目经历
- project_name (str)：项目名。
- project_role (str)：在项目中的角色（如"项目经理"、"后端开发"）。
- project_date (str)：项目时间段，原文照抄简历写法即可（如"2021.03 - 2022.06"、"2023年至今"）。
- project_desc (str)：项目描述。
- project_duty (str)：本人在该项目的具体职责（与 project_desc 区别：desc 写项目本身，duty 写"我做了什么"）。
- project_result (str)：项目成果/产出。

## honors[] 荣誉
- honor_name (str)：荣誉名（如"国家奖学金"、"优秀员工"）。
- honor_date (str|null)：获得日期，YYYY-MM-DD，缺月份补 -01；只有年份用 YYYY-01-01。
- honor_level (str)：等级，"国家级" / "省级" / "市级" / "校级" / "公司级"。

## skills[] 技能/证书 ★合并规则
**重要：相同 skill_type 的技能要合并到一条记录里**，多个 skill_name 用顿号"、"连接。例如：
- ❌ 错：[{"skill_type":"语言","skill_name":"Python"},{"skill_type":"语言","skill_name":"Java"}]
- ✅ 对：[{"skill_type":"语言","skill_name":"Python、Java、SQL"}]

字段：
- skill_type (str)：分类，只能是 "专业" / "语言" / "工具" / "证书" 之一。
- skill_name (str)：技能/证书名（合并后用"、"连接）。
- proficiency (str)：熟练度，"精通" / "熟练" / "了解"。证书类不写。

## job_intention 求职意向（单对象，非数组）
- target_position (str)：目标岗位。
- target_city (str)：期望工作城市。
- expected_salary (str)：期望薪资（如"15-20K"）。
- job_status (str)：求职状态（"在职-看机会" / "离职-随时到岗" / "应届"）。

# 通用规则

1. 输出**严格 JSON 对象**（不是数组），不要 markdown 代码块、不要解释文字。
2. 除 name 外，任何字段无法识别就返回 null，不要编造；name 必须返回有效字符串。
3. 日期统一 YYYY-MM-DD，缺月份用 -01 补齐，"至今/在职" → null。
4. 手机号、邮箱、微信若简历有多个，取主要一个。
5. 数组字段（educations / work_experiences 等）若简历完全没有该信息就返回空数组 []，不是 null。
"""


SYSTEM_PROMPT = _SYSTEM_PROMPT_TEMPLATE.replace("{current_year}", str(datetime.now().year))


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

    def parse_resume_text(self, raw_text: str, source_name: str | None = None) -> CandidateCreate | None:
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
                    {"role": "user", "content": f"附件文件名：{source_name or '-'}\n\n请解析以下简历：\n\n{raw_text[:MAX_RESUME_TEXT_CHARS]}"},
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
            data = _unwrap_candidate_envelope(data)
            data = _ensure_candidate_name(data, source_name)
            return CandidateCreate(**data)
        except json.JSONDecodeError as exc:
            logger.warning("AI 返回内容非合法 JSON：{}", exc)
            return None
        except Exception as exc:
            logger.error("AI 解析简历失败：{}", exc)
            raise

    def match_candidates(
        self, position_requirements: str, candidates: list[dict]
    ) -> list[dict]:
        """AI 匹配岗位需求与候选人列表，返回全部候选人的评分结果。

        每个 candidate dict 应包含：candidate_id, name, education_level, current_city,
        position (最近职位), skills (技能摘要), work_summary (工作经历摘要)。
        返回：[{"candidate_id": int, "match_score": 0-100, "reason": str}, ...]
        """
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置")
        candidates_text = "\n".join(
            f"ID={c.get('candidate_id')} 姓名={c.get('name')} "
            f"学历={c.get('education_level', '-')} 城市={c.get('current_city', '-')} "
            f"最近职位={c.get('position', '-')} 技能={c.get('skills', '-')} "
            f"工作摘要={c.get('work_summary', '-')}"
            for c in candidates
        )
        prompt = (
            f"岗位要求：\n{position_requirements}\n\n"
            f"候选人列表（共 {len(candidates)} 人）：\n{candidates_text}\n\n"
            "请对每位候选人评估与该岗位的匹配度（0-100 分），并给出简短评语。\n"
            "输出 JSON 数组，包含所有候选人：\n"
            '[{"candidate_id": ID, "match_score": 0-100, "reason": "一句话匹配评语"}]'
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是招聘匹配助手。对每位候选人评估岗位匹配度（0-100），输出完整 JSON 数组，不要遗漏任何候选人。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                timeout=120,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            results = json.loads(content.strip())
            if isinstance(results, dict) and "candidates" in results:
                results = results["candidates"]
            if isinstance(results, dict) and "results" in results:
                results = results["results"]
            return results if isinstance(results, list) else []
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

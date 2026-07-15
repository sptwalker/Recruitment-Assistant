"""AI 简历结构化解析服务。

使用 OpenAI 兼容 API（DeepSeek / 通义千问 / OpenAI）将纯文本简历
结构化为标准字段，写入 SQLite resume_archive 数据库。
"""

from __future__ import annotations

import json
import re
import time as _time
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

    def parse_resume_text(self, raw_text: str, source_name: str | None = None, retry: int = 2) -> CandidateCreate | None:
        """调 LLM 将纯文本简历结构化为 CandidateCreate。

        Args:
            raw_text: 简历原始文本
            source_name: 文件名
            retry: 失败重试次数，默认 2 次
        """
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置，请在 .env 文件中设置 AI_API_KEY")
        if not raw_text or len(raw_text.strip()) < 20:
            return None

        # ✨ 重试机制
        for attempt in range(retry + 1):
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

                # ✨ 增强 JSON 清理逻辑
                content = self._clean_json_response(content)

                data = json.loads(content)
                data = _unwrap_candidate_envelope(data)
                data = _ensure_candidate_name(data, source_name)

                # ✨ 验证必填字段
                if not data.get('name'):
                    if attempt < retry:
                        logger.warning(f"AI 解析缺少必填字段 name（尝试 {attempt+1}/{retry+1}）")
                        continue
                    raise ValueError("姓名字段缺失")

                return CandidateCreate(**data)

            except json.JSONDecodeError as exc:
                if attempt < retry:
                    logger.warning(f"AI 返回内容非合法 JSON（尝试 {attempt+1}/{retry+1}）：{exc}")
                    continue
                logger.error(f"AI 返回内容非合法 JSON（最终失败）：{exc}")
                return None
            except ValueError as exc:
                if attempt < retry:
                    logger.warning(f"数据验证失败（尝试 {attempt+1}/{retry+1}）：{exc}")
                    continue
                logger.error(f"数据验证失败（最终失败）：{exc}")
                return None
            except Exception as exc:
                if attempt < retry:
                    logger.warning(f"AI 解析简历失败（尝试 {attempt+1}/{retry+1}）：{exc}")
                    continue
                logger.error(f"AI 解析简历失败（最终失败）：{exc}")
                raise

        return None

    def _clean_json_response(self, content: str) -> str:
        """清理 AI 返回的 JSON 响应"""
        # 去掉 markdown 代码块标记
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]

        # 去掉可能的 json 标记
        if content.startswith("json"):
            content = content[4:]

        return content.strip()

    def match_candidates(
        self, position_requirements: str, candidates: list[dict], debug_logger=None
    ) -> list[dict]:
        """AI 匹配岗位需求与候选人列表，返回全部候选人的评分结果。

        Args:
            position_requirements: 岗位要求描述
            candidates: 候选人字典列表
            debug_logger: 可选的 MatchDebugLogger 实例，用于记录调试信息
        """
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置")

        logger.info("[岗位匹配] ===== 开始匹配 =====")
        logger.info("[岗位匹配] 模型={}, base_url={}", self.model, self.base_url)
        logger.info("[岗位匹配] 候选人数量: {}", len(candidates))
        logger.info("[岗位匹配] JD 前200字: {}", position_requirements[:200])

        if debug_logger:
            debug_logger.log_ai_request(0, len(candidates), position_requirements[:200])

        candidates_text = "\n".join(
            f"ID={c.get('candidate_id')} 姓名={c.get('name')} "
            f"学历={c.get('education_level', '-')} 城市={c.get('current_city', '-')} "
            f"最近职位={c.get('position', '-')} 技能={c.get('skills', '-')} "
            f"核心技能={c.get('core_skills', '-')} 工作年限={c.get('years_of_experience', '-')} "
            f"工作摘要={c.get('work_summary', '-')} "
            f"项目经验={c.get('projects', '-')} 荣誉证书={c.get('honors', '-')}"
            for c in candidates
        )
        prompt = (
            f"岗位要求：\n{position_requirements}\n\n"
            f"候选人列表（共 {len(candidates)} 人）：\n{candidates_text}\n\n"
            "请对每位候选人进行多维度评估，每个维度独立评分（0-100 分）：\n"
            "1. skill_match (技能匹配度): 技能与岗位要求的匹配程度\n"
            "2. experience_match (经验匹配度): 工作经验与岗位要求的匹配程度\n"
            "3. education_match (学历匹配度): 学历背景与岗位要求的匹配程度\n"
            "4. location_match (地域匹配度): 工作城市与岗位地点的匹配程度\n\n"
            "严格输出 JSON 对象，不要输出任何其他文字。格式：\n"
            '{"results": [{\n'
            '  "candidate_id": ID,\n'
            '  "match_score": 综合得分(0-100),\n'
            '  "dimensions": {\n'
            '    "skill_match": 分数,\n'
            '    "experience_match": 分数,\n'
            '    "education_match": 分数,\n'
            '    "location_match": 分数\n'
            '  },\n'
            '  "reason": "一句话综合评语"\n'
            '}]}'
        )
        logger.info("[岗位匹配] Prompt 总长度: {} 字符", len(prompt))

        MATCH_TIMEOUT = 60
        MATCH_MAX_RETRIES = 2
        MATCH_RETRY_BACKOFF = [3, 6]

        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        resp = None
        for attempt in range(MATCH_MAX_RETRIES + 1):
            try:
                logger.info("[岗位匹配] 正在调用 AI API... (第{}次尝试)", attempt + 1)
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是招聘匹配助手。对每位候选人进行多维度评估（技能/经验/学历/地域），严格只输出 JSON，不要输出任何解释文字。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    timeout=MATCH_TIMEOUT,
                    response_format={"type": "json_object"},
                )
                break
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                if attempt < MATCH_MAX_RETRIES:
                    wait = MATCH_RETRY_BACKOFF[attempt]
                    logger.warning("[岗位匹配] 第{}次重试 (等{}s): {} — {}",
                                   attempt + 1, wait, type(exc).__name__, exc)
                    _time.sleep(wait)
                    continue
                logger.error("[岗位匹配] 重试耗尽, 最终失败: {} — {}", type(exc).__name__, exc)
                raise
            except APIStatusError as exc:
                if exc.status_code >= 500 and attempt < MATCH_MAX_RETRIES:
                    wait = MATCH_RETRY_BACKOFF[attempt]
                    logger.warning("[岗位匹配] 服务端错误 {} 第{}次重试 (等{}s): {}",
                                   exc.status_code, attempt + 1, wait, exc)
                    _time.sleep(wait)
                    continue
                logger.error("[岗位匹配] API 错误 {}: {}", exc.status_code, exc)
                raise
            except Exception as exc:
                logger.error("[岗位匹配] AI 调用异常: {} — {}", type(exc).__name__, exc)
                import traceback
                logger.error("[岗位匹配] 完整堆栈:\n{}", traceback.format_exc())
                raise

        content = resp.choices[0].message.content
        logger.info("[岗位匹配] AI 返回 content 类型={}, 长度={}",
                    type(content).__name__, len(content) if content else 0)
        if not content:
            logger.error("[岗位匹配] AI 返回 content 为空!")
            return []

        content = content.strip()
        logger.info("[岗位匹配] AI 原始返回全文:\n{}", content)

        finish_reason = resp.choices[0].finish_reason if resp.choices else "unknown"
        logger.info("[岗位匹配] finish_reason={}", finish_reason)
        if resp.usage:
            logger.info("[岗位匹配] tokens: prompt={}, completion={}, total={}",
                        resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens)

        results = self._extract_match_results(content)

        if debug_logger:
            debug_logger.log_ai_response(0, results)

        for r in results:
            if "dimensions" not in r:
                score = r.get("match_score", 50)
                r["dimensions"] = {
                    "skill_match": score,
                    "experience_match": score,
                    "education_match": score,
                    "location_match": score,
                }

        logger.info("[岗位匹配] 解析结果: {} 条匹配记录", len(results))
        if results:
            first = results[0]
            logger.info("[岗位匹配] 首条示例: candidate_id={}, score={}, reason={}",
                        first.get("candidate_id"), first.get("match_score"),
                        str(first.get("reason", ""))[:80])
        logger.info("[岗位匹配] ===== 匹配结束 =====")
        return results

    @staticmethod
    def _extract_match_results(content: str) -> list[dict]:
        """从 AI 返回内容中提取匹配结果列表，兼容多种格式。"""
        import re

        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        if content.startswith("json"):
            content = content[4:].strip()

        try:
            parsed = json.loads(content)
            logger.info("[岗位匹配] JSON 解析成功, 类型={}", type(parsed).__name__)
            if isinstance(parsed, list):
                logger.info("[岗位匹配] 直接为数组, 长度={}", len(parsed))
                return parsed
            if isinstance(parsed, dict):
                logger.info("[岗位匹配] 为字典, keys={}", list(parsed.keys()))
                for key in ("results", "candidates", "data", "items"):
                    if key in parsed and isinstance(parsed[key], list):
                        logger.info("[岗位匹配] 从 key='{}' 提取, 长度={}", key, len(parsed[key]))
                        return parsed[key]
                for k, v in parsed.items():
                    if isinstance(v, list):
                        logger.info("[岗位匹配] 从首个 list key='{}' 提取, 长度={}", k, len(v))
                        return v
                logger.warning("[岗位匹配] 字典中未找到任何 list 值")
            return []
        except json.JSONDecodeError as e:
            logger.warning("[岗位匹配] 直接 JSON 解析失败: {}", e)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for key in ("results", "candidates", "data", "items"):
                    if key in parsed and isinstance(parsed[key], list):
                        return parsed[key]
                # dict 只有一个 list 值
                for v in parsed.values():
                    if isinstance(v, list):
                        return v
            return []
        except json.JSONDecodeError:
            pass

        # 回退：用正则提取最大的 JSON 数组或对象
        arrays = re.findall(r'\[[\s\S]*\]', content)
        for arr in sorted(arrays, key=len, reverse=True):
            try:
                parsed = json.loads(arr)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue

        objects = re.findall(r'\{[\s\S]*\}', content)
        for obj in sorted(objects, key=len, reverse=True):
            try:
                parsed = json.loads(obj)
                if isinstance(parsed, dict):
                    for key in ("results", "candidates", "data", "items"):
                        if key in parsed and isinstance(parsed[key], list):
                            return parsed[key]
            except json.JSONDecodeError:
                continue

        logger.warning("[岗位匹配] 无法从 AI 返回中提取 JSON: {}", content[:300])
        return []

    _OUTLINE_SYSTEM_PROMPT = """\
你是资深企业招聘面试官 & 人才测评专家，精通胜任力模型、STAR 行为面试法、结构化面试设计，擅长基于岗位真实需求和候选人个人简历，定制可直接现场使用的专属面试大纲。

一、核心任务
请根据我提供的【岗位需求（JD）】和【候选人简历】，为该特定候选人生成一份精准匹配岗位、深挖真实能力的专业结构化面试大纲。
要求：所有问题紧扣岗位需求的核心要求、绑定候选人过往经历，拒绝空泛通用问题，适配岗位层级（基层 / 骨干 / 管理）。

二、我将提供的素材
岗位需求（JD）：岗位职责、核心任职要求、必备技能、考核指标、岗位层级
候选人简历：基本信息、工作经历、项目经验、核心业绩、技能、求职意向
已有面试评价：之前面试环节中面试官的历史评价信息

三、强制输出格式（严格按以下结构生成，不可修改框架）

【XX 岗位】专属结构化面试大纲（候选人：XXX）

一、面试基础信息
应聘岗位：
候选人核心画像：（1 句话总结：优势匹配点 + 潜在短板）
岗位核心胜任力：（提炼 JD 3-5 项核心要求）
建议面试时长：XX 分钟

二、面试考核维度 & 评分权重（总分 100 分）
岗位专业能力：XX 分
实操 / 项目经验：XX 分
通用软素质：XX 分
求职动机 & 匹配度：XX 分
潜力 & 稳定性：XX 分

三、分模块面试问题（STAR 行为化提问，可直接提问）

模块 1：破冰 & 基础认知（3-5 分钟）
请用 3 分钟自我介绍，重点讲与本岗位相关的核心经历
你为何应聘该岗位？对岗位核心工作的理解是什么？

模块 2：岗位专业能力考核（紧扣 JD 必备技能）
【定制问题 1：结合岗位要求 + 候选人技能】
【定制问题 2：针对岗位核心工作场景】
【定制问题 3：岗位必备专业知识 / 实操能力】

模块 3：过往经历深挖（验证简历真实性 + 实操能力）
针对候选人简历核心项目 / 工作，深度追问
请详细说明你简历中【XX 项目 / XX 工作】的背景、你的职责、执行过程、结果
该工作中你遇到的最大困难是什么？如何解决的？
工作成果有无量化数据？产出了什么价值？
结合本岗位需求，这类工作你如何快速落地？

模块 4：软素质 & 职业素养（按岗位匹配设计）
【沟通 / 协作 / 抗压 / 执行力等】定制问题
【问题解决 / 逻辑思维 / 责任心等】定制问题

模块 5：动机、稳定性 & 职业规划
离职 / 求职核心原因是什么？
未来 1-3 年职业规划？为何选择本岗位 / 行业？
对薪资、工作节奏、加班等预期？

模块 6：候选人反问环节
预留 5 分钟解答候选人疑问

四、面试评分参考要点
专业能力：是否匹配岗位必备技能
经验匹配：过往经历能否直接胜任工作
软素质：沟通、逻辑、抗压、协作是否达标
匹配度：动机、规划、价值观与岗位 / 公司契合度

五、面试注意事项
重点验证简历真实性，对模糊经历深度追问
聚焦岗位核心需求，不偏离考核维度
用 STAR 法则深挖行为事件，少用假设性问题

四、执行规则
定制化第一：所有问题必须结合候选人简历具体经历+岗位 JD，禁止通用题库
层级适配：基层重执行、骨干重专业、管理重统筹 / 决策 / 带人
行为化提问：以 "你做过什么、怎么做、结果如何" 为主，拒绝空泛主观题"""

    def generate_interview_outline(self, candidate_info: str, position: str, requirements: str = "", evaluations: str = "") -> str:
        """AI 生成面试大纲。"""
        if not self.is_configured:
            raise RuntimeError("AI API Key 未配置")
        prompt_parts = [f"【候选人简历】\n{candidate_info}"]
        if requirements:
            prompt_parts.append(f"【岗位需求（JD）】\n岗位名称：{position}\n{requirements}")
        else:
            prompt_parts.append(f"【岗位需求（JD）】\n岗位名称：{position}")
        if evaluations:
            prompt_parts.append(f"【已有面试评价】\n{evaluations}")
        prompt_parts.append("请根据以上素材，严格按照系统提示中的强制输出格式，生成该候选人的专属结构化面试大纲。")
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._OUTLINE_SYSTEM_PROMPT},
                    {"role": "user", "content": "\n\n".join(prompt_parts)},
                ],
                temperature=0.3,
                timeout=120,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("AI 生成面试大纲失败：{}", exc)
            return f"生成失败：{exc}"

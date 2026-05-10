import re

from scrapling.parser import Selector

UNKNOWN = "待识别"
PROFILE_KEYWORDS = (
    "男", "女", "求职", "应聘", "职位", "岗位", "电话", "手机", "岁", "经验",
    "本科", "专科", "硕士", "博士", "学历", "工作年限", "期望",
)
EXCLUDE_KEYWORDS = (
    "快捷回复", "发送", "表情", "聊天记录", "附件简历", "要附件简历", "查看简历附件",
    "下载简历", "消息", "沟通", "已读", "未读", "打招呼", "请输入", "复制",
)
JOB_LABELS = (
    "求职岗位", "求职职位", "应聘岗位", "应聘职位", "期望职位", "期望岗位", "目标职位", "目标岗位", "职位", "岗位",
)
JOB_KEYWORDS = (
    "工程师", "经理", "主管", "专员", "顾问", "运营", "销售", "开发", "产品", "设计",
    "会计", "人事", "行政", "客服", "教师", "司机", "助理", "总监", "招聘", "采购",
    "算法", "测试", "前端", "后端", "架构", "实施", "运维", "财务", "出纳", "法务",
)
NAME_STOP_TOKENS = EXCLUDE_KEYWORDS + (
    "电话", "手机号", "求职", "职位", "岗位", "本科", "专科", "硕士", "博士", "经验",
)


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_probably_person_name(value: str | None) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    return bool(re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", text) or re.fullmatch(r"[A-Za-z][A-Za-z .·-]{1,30}", text))


def clean_candidate_name(value: str | None) -> str:
    text = normalize_text(value)
    if not text or text == UNKNOWN:
        return ""
    for part in re.split(r"[｜|/\\,，;；:：\n\r\t ]+", text):
        part = part.strip(" ·-—_()（）[]【】")
        if not part or any(token in part for token in NAME_STOP_TOKENS):
            continue
        if re.search(r"\d|岁|年|男|女", part):
            continue
        if _is_probably_person_name(part):
            return part
    return ""


def _clean_job_title(value: str | None, candidate_name: str = "") -> str:
    text = normalize_text(value).strip(" -—｜|:：")
    if not text or text == UNKNOWN:
        return ""
    company_noise = ("有限公司", "分公司", "集团", "科技", "公司", "企业", "中心", "事业部", "工作室", "系统集成")
    section_noise = ("工作经历", "项目经历", "教育经历", "实习经历", "培训经历", "校园经历")
    text = re.sub(r"^(" + "|".join(JOB_LABELS) + r")[:： ]*", "", text).strip(" -—｜|")
    text = re.sub(r"^(" + "|".join(section_noise) + r")\s*[（(]?\s*\d+(?:\.\d+)?\s*年\s*[）)]?\s*", "", text).strip(" -—｜|")
    text = re.split(r"电话|手机|性别|男|女|\d{2,}|岁|经验|本科|专科|硕士|博士|学历", text)[0].strip(" -—｜|")
    parts = [part.strip(" -—｜|/\\,，;；:：()（）[]【】") for part in re.split(r"[·•|｜/\\,，;；\n\r\t]+", text)]
    candidates = [part for part in parts if part]
    candidates.append(text)
    for part in reversed(candidates):
        if not (2 <= len(part) <= 40):
            continue
        if any(token in part for token in company_noise + section_noise):
            continue
        if any(keyword.lower() in part.lower() for keyword in JOB_KEYWORDS):
            text = part
            break
    if not text or text == candidate_name or _is_probably_person_name(text):
        return ""
    if any(token in text for token in EXCLUDE_KEYWORDS + company_noise + section_noise):
        return ""
    return text if 2 <= len(text) <= 40 else ""


def _select_profile_lines(html: str) -> list[str]:
    page = Selector(html)
    raw_lines: list[str] = []
    selectors = (
        '[class*="candidate"], [class*="profile"], [class*="detail"], [class*="resume"], '
        '[class*="user"], [class*="person"], [class*="talent"], [class*="card"], [class*="info"], '
        'aside, section, header, article, div, span, p'
    )
    for node in page.css(selectors):
        text = normalize_text(node.text())
        if not text or len(text) > 500:
            continue
        if any(token in text for token in EXCLUDE_KEYWORDS):
            continue
        if any(token in text for token in PROFILE_KEYWORDS) or re.search(r"1[3-9]\d{9}", text):
            for part in re.split(r"\n| {2,}|\t", text):
                line = normalize_text(part)
                if line and len(line) <= 220 and not any(token in line for token in EXCLUDE_KEYWORDS):
                    raw_lines.append(line)
    seen = set()
    lines = []
    for line in sorted(raw_lines, key=lambda item: (-_line_profile_score(item), len(item), item)):
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= 30:
            break
    return lines


def _line_profile_score(line: str) -> int:
    score = 0
    if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", line):
        score += 8
    if re.search(r"性别[:： ]*[男女]|(^|[^\u4e00-\u9fa5])[男女]([^\u4e00-\u9fa5]|$)", line):
        score += 5
    if any(label in line for label in JOB_LABELS):
        score += 5
    if any(token in line for token in JOB_KEYWORDS):
        score += 4
    if clean_candidate_name(line):
        score += 3
    return score


def _extract_gender(text: str) -> str:
    if re.search(r"(^|[^\u4e00-\u9fa5])男([^\u4e00-\u9fa5]|$)|性别[:： ]*男", text):
        return "男"
    if re.search(r"(^|[^\u4e00-\u9fa5])女([^\u4e00-\u9fa5]|$)|性别[:： ]*女", text):
        return "女"
    return UNKNOWN


def _extract_job_title(lines: list[str], candidate_name: str = "") -> str:
    label_pattern = "|".join(JOB_LABELS)
    for line in lines:
        match = re.search(rf"(?:{label_pattern})[:： ]*([^｜|,，;；\n\r]+)", line)
        if match:
            job_title = _clean_job_title(match.group(1), candidate_name)
            if job_title:
                return job_title
    for line in lines:
        if not any(token in line for token in JOB_KEYWORDS):
            continue
        job_title = _clean_job_title(line, candidate_name)
        if job_title:
            return job_title
    return UNKNOWN


def extract_candidate_info(html: str, fallback_signature: str = "") -> dict:
    lines = _select_profile_lines(html)
    profile_text = "\n".join(lines)
    merged = " ".join(lines) or fallback_signature
    phone_match = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", merged)

    name = ""
    for line in lines:
        name = clean_candidate_name(line)
        if name:
            break
    if not name:
        name = clean_candidate_name(fallback_signature)

    return {
        "name": name or UNKNOWN,
        "gender": _extract_gender(merged),
        "job_title": _extract_job_title(lines, name),
        "phone": phone_match.group(1) if phone_match else UNKNOWN,
        "profile_text": profile_text,
        "extractor": "scrapling",
    }

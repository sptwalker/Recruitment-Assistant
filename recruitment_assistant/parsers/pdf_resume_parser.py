import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from docx import Document
from pypdf import PdfReader

PHONE_RE = re.compile(r"(?<!\d)(?:1[3-9]\d{9})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DEGREES = ["博士", "硕士", "研究生", "本科", "大专", "专科", "高中", "中专"]
SECTION_KEYWORDS = ["个人信息", "求职意向", "工作经历", "项目经历", "教育经历", "自我评价", "技能", "证书"]
NOISE_KEYWORDS = [
    "在线", "沟通", "聊天", "未读", "未联系", "已联系", "打招呼", "要附件简历", "已向对方要附件简历",
    "查看简历", "查看附件简历", "设置备注", "不合适", "待识别",
    "快速处理", "新招呼", "全部职位", "筛选", "批量", "智联", "简历", "附件", "下载",
]
JOB_HINTS = [
    "工程师", "开发", "经理", "主管", "专员", "助理", "运营", "产品", "设计", "销售", "财务", "人事", "行政", "顾问", "总监",
    "算法", "测试", "前端", "后端", "架构", "实施", "运维", "会计", "出纳", "法务", "教师", "司机", "客服", "Unity", "Java", "Python",
]


@dataclass
class ParsedResume:
    source_file: str
    text: str
    name: str | None = None
    job_title: str | None = None
    phone: str | None = None
    email: str | None = None
    current_city: str | None = None
    highest_degree: str | None = None
    years_of_experience: Decimal | None = None
    current_company: str | None = None
    current_position: str | None = None
    expected_position: str | None = None
    skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "name": self.name,
            "job_title": self.job_title,
            "phone": self.phone,
            "email": self.email,
            "current_city": self.current_city,
            "highest_degree": self.highest_degree,
            "years_of_experience": str(self.years_of_experience) if self.years_of_experience else None,
            "current_company": self.current_company,
            "current_position": self.current_position,
            "expected_position": self.expected_position,
            "skills": self.skills,
            "text_preview": self.text[:1000],
        }


def normalize_text(text: str) -> str:
    lines = []
    for line in text.replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def extract_pdf_text(file_path: str | Path) -> str:
    reader = PdfReader(str(file_path))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return normalize_text("\n".join(parts))


def extract_docx_text(file_path: str | Path) -> str:
    document = Document(str(file_path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            row_text = " ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                parts.append(row_text)
    return normalize_text("\n".join(parts))


def extract_doc_text(file_path: str | Path) -> str:
    data = Path(file_path).read_bytes()
    candidates = []
    for encoding in ("utf-16le", "gb18030", "utf-8"):
        try:
            text = data.decode(encoding, errors="ignore")
        except Exception:
            continue
        cleaned = "".join(
            ch if (ch == "\n" or ch == "\t" or (ch.isprintable() and not 0xD800 <= ord(ch) <= 0xDFFF)) else "\n"
            for ch in text
        )
        cleaned = normalize_text(cleaned)
        score = sum(1 for ch in cleaned if "\u4e00" <= ch <= "\u9fff")
        if score:
            candidates.append((score, cleaned))
    return max(candidates, key=lambda item: item[0])[1] if candidates else ""


def parse_resume_file(file_path: str | Path, candidate_signature: str | None = None) -> ParsedResume:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
    elif suffix == ".docx":
        text = extract_docx_text(path)
    elif suffix == ".doc":
        text = extract_doc_text(path)
    else:
        raise ValueError(f"暂不支持解析 {suffix} 文件")
    return parse_resume_text(path, text, candidate_signature=candidate_signature)


def parse_resume_pdf(file_path: str | Path) -> ParsedResume:
    return parse_resume_file(file_path)


def parse_resume_text(path: Path, text: str, candidate_signature: str | None = None) -> ParsedResume:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    parsed = ParsedResume(source_file=str(path), text=text)
    parsed.phone = find_phone(text)
    parsed.email = find_email(text)
    parsed.name = find_name(lines, parsed.phone, parsed.email)
    parsed.highest_degree = find_highest_degree(text)
    parsed.years_of_experience = find_years_of_experience(text)
    parsed.current_city = find_city(lines)
    parsed.expected_position = find_expected_position(lines)
    parsed.current_company, parsed.current_position = find_current_work(lines)
    parsed.skills = find_skills(text)

    name_from_file, job_from_file = infer_name_and_job_from_filename(path.name)
    name_from_sig, job_from_sig = clean_candidate_signature(candidate_signature or "")
    parsed.name = first_valid_name(parsed.name, name_from_file, name_from_sig)
    parsed.job_title = first_non_empty(job_from_file, job_from_sig, parsed.expected_position, parsed.current_position)
    return parsed



def find_job_title_from_header(lines: list[str], name: str | None = None) -> str | None:
    skip_prefixes = ("研究方向", "专业方向", "主修", "毕业", "学历", "年龄", "电话", "邮箱", "现所在地", "联系信息")
    skip_contains = ("教育经历", "工作经历", "项目经历", "专业", "大学", "学院", "本科", "硕士", "博士", "大专", "毕业时间")
    invalid_chars = "。；;，,、"
    for line in lines[:18]:
        line = line.strip(" -—｜|")
        if not line or (name and line == name):
            continue
        if line.startswith(skip_prefixes) or any(token in line for token in skip_contains):
            continue
        if len(line) <= 40 and not any(char in line for char in invalid_chars) and any(hint.lower() in line.lower() for hint in JOB_HINTS):
            return line
        for part in split_resume_tokens(line):
            if part == name or first_valid_name(part) == part:
                continue
            if len(part) <= 30 and not any(char in part for char in invalid_chars) and any(hint.lower() in part.lower() for hint in JOB_HINTS):
                return part
    return None


def clean_candidate_signature(signature: str) -> tuple[str | None, str | None]:
    if not signature:
        return None, None
    text = re.sub(r"\s+", " ", signature).strip()
    text = re.sub(r"pos:\d+:\d+:\d+:\d+", " ", text)
    parts = split_resume_tokens(text)
    parts = [part for part in parts if part and not is_noise_token(part)]
    name = first_valid_name(*parts[:6])
    job = None
    for part in parts:
        if part == name or re.search(r"\d+岁|\d+年|本科|硕士|博士|大专|深圳|广州|上海|北京", part):
            continue
        if any(hint in part for hint in JOB_HINTS) and 2 <= len(part) <= 40:
            job = part
            break
    return name, job


def infer_name_and_job_from_filename(filename: str) -> tuple[str | None, str | None]:
    stem = Path(filename).stem
    stem = re.sub(r"^zhilian_\d{8}_\d{6}_", "", stem)
    stem = re.sub(r"_[0-9a-f]{8,}$", "", stem, flags=re.I)
    parts = split_resume_tokens(stem)
    parts = [part for part in parts if part and not is_noise_token(part)]
    name = first_valid_name(*parts[:4])
    job = None
    for part in parts:
        if part == name or re.fullmatch(r"\d+岁", part) or part in {"深圳", "广州", "上海", "北京"}:
            continue
        if any(hint in part for hint in JOB_HINTS) or (2 <= len(part) <= 40 and not re.fullmatch(r"[0-9a-f]{8,}", part, re.I)):
            job = part
            break
    return name, job


def split_resume_tokens(text: str) -> list[str]:
    text = re.sub(r"[()（）\[\]【】]", " ", text)
    return [item.strip(" _-—|｜/\\,，;；:") for item in re.split(r"[_\-|｜/\\,，;；\n\t ]+", text) if item.strip()]


def is_noise_token(value: str) -> bool:
    if any(keyword in value for keyword in NOISE_KEYWORDS):
        return True
    if re.fullmatch(r"[0-9a-f]{12,}", value, re.I):
        return True
    if re.fullmatch(r"\d{4,}", value):
        return True
    return False


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return None


def first_valid_name(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        cleaned = re.sub(r"[|｜_/\\,，;；:].*", "", str(value)).strip()
        if any(keyword in cleaned for keyword in NOISE_KEYWORDS + JOB_HINTS + SECTION_KEYWORDS):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5·]{2,8}", cleaned):
            return cleaned
    return None


def find_phone(text: str) -> str | None:
    match = PHONE_RE.search(text)
    return match.group(0) if match else None


def find_email(text: str) -> str | None:
    match = EMAIL_RE.search(text)
    return match.group(0) if match else None


def find_name(lines: list[str], phone: str | None, email: str | None) -> str | None:
    for line in lines[:20]:
        if phone and phone in line:
            continue
        if email and email in line:
            continue
        if any(keyword in line for keyword in SECTION_KEYWORDS + NOISE_KEYWORDS + JOB_HINTS):
            continue
        for part in split_resume_tokens(line):
            name = first_valid_name(part)
            if name:
                return name
    return None


def find_highest_degree(text: str) -> str | None:
    for degree in DEGREES:
        if degree in text:
            if degree == "研究生":
                return "硕士"
            if degree == "专科":
                return "大专"
            return degree
    return None


def find_years_of_experience(text: str) -> Decimal | None:
    for pattern in [r"(\d+(?:\.\d+)?)\s*年(?:以上)?工作经验", r"工作经验[:：]?\s*(\d+(?:\.\d+)?)\s*年", r"(\d+(?:\.\d+)?)\s*年经验"]:
        match = re.search(pattern, text)
        if match:
            return Decimal(match.group(1))
    return None


def find_city(lines: list[str]) -> str | None:
    for line in lines[:40]:
        match = re.search(r"(?:现居|所在地|当前城市|居住地|城市)[:： ]*([\u4e00-\u9fa5]{2,8})", line)
        if match:
            return match.group(1)
    return None


def clean_job_title(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -—｜|:：")
    if not text:
        return None
    company_noise = ["有限公司", "分公司", "集团", "科技", "公司", "企业", "中心", "事业部", "工作室", "系统集成"]
    section_noise = ["工作经历", "项目经历", "教育经历", "实习经历", "培训经历", "校园经历"]
    text = re.sub(r"^(期望职位|求职意向|应聘职位|求职岗位|应聘岗位|目标职位|目标岗位|职位|岗位)[:： ]*", "", text).strip(" -—｜|")
    text = re.sub(r"^(" + "|".join(section_noise) + r")\s*[（(]?\s*\d+(?:\.\d+)?\s*年\s*[）)]?\s*", "", text).strip(" -—｜|")
    text = re.split(r"电话|手机|性别|姓名|男|女|\d{2,}|岁|经验|本科|专科|硕士|博士|学历|在线|沟通|附件|简历", text)[0].strip(" -—｜|")
    parts = [part.strip(" -—｜|/\\,，;；:：()（）[]【】") for part in re.split(r"[·•|｜/\\,，;；\n\r\t]+", text)]
    candidates = [part for part in parts if part]
    candidates.append(text)
    for part in reversed(candidates):
        if not (2 <= len(part) <= 40):
            continue
        if any(token in part for token in company_noise + section_noise):
            continue
        if any(hint.lower() in part.lower() for hint in JOB_HINTS):
            return part
    return text if 2 <= len(text) <= 40 and not any(token in text for token in company_noise + section_noise) else None


def find_expected_position(lines: list[str]) -> str | None:
    for line in lines:
        if "期望职位" in line or "求职意向" in line or "应聘职位" in line:
            value = re.sub(r".*?(期望职位|求职意向|应聘职位)[:： ]*", "", line).strip()
            return clean_job_title(value)
    return None


def find_current_work(lines: list[str]) -> tuple[str | None, str | None]:
    for index, line in enumerate(lines):
        if "工作经历" not in line:
            continue
        source_line = line if any(separator in line for separator in ["·", "•", "|", "｜"]) else (lines[index + 1] if index + 1 < len(lines) else "")
        cleaned_line = re.sub(r"^工作经历\s*[（(]?\s*\d+(?:\.\d+)?\s*年\s*[）)]?\s*", "", source_line).strip(" -—｜|")
        parts = [part.strip() for part in re.split(r"[·•|｜]", cleaned_line) if part.strip()]
        company = parts[0] if parts else None
        position = clean_job_title(parts[-1] if len(parts) > 1 else "")
        if company and len(company) <= 80 and not any(keyword in company for keyword in SECTION_KEYWORDS):
            return company, position
    return None, None


def find_skills(text: str) -> list[str]:
    common_skills = ["Python", "Java", "C++", "C#", "JavaScript", "TypeScript", "React", "Vue", "SQL", "MySQL", "PostgreSQL", "Oracle", "Redis", "Docker", "Kubernetes", "Linux", "Excel", "Photoshop", "CAD", "SolidWorks", "PLC", "PMP", "销售", "运营", "招聘", "人事", "财务"]
    lower_text = text.lower()
    return [skill for skill in common_skills if skill.lower() in lower_text]

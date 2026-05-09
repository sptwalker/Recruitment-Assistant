import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader


PHONE_RE = re.compile(r"(?<!\d)(?:1[3-9]\d{9})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DEGREES = ["博士", "硕士", "研究生", "本科", "大专", "专科", "高中", "中专"]
SECTION_KEYWORDS = [
    "个人信息",
    "求职意向",
    "工作经历",
    "项目经历",
    "教育经历",
    "自我评价",
    "技能",
    "证书",
]


@dataclass
class ParsedResume:
    source_file: str
    text: str
    name: str | None = None
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


def extract_pdf_text(file_path: str | Path) -> str:
    reader = PdfReader(str(file_path))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return normalize_text("\n".join(parts))


def normalize_text(text: str) -> str:
    lines = []
    for line in text.replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def parse_resume_pdf(file_path: str | Path) -> ParsedResume:
    path = Path(file_path)
    text = extract_pdf_text(path)
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
    return parsed


def find_phone(text: str) -> str | None:
    match = PHONE_RE.search(text)
    return match.group(0) if match else None


def find_email(text: str) -> str | None:
    match = EMAIL_RE.search(text)
    return match.group(0) if match else None


def find_name(lines: list[str], phone: str | None, email: str | None) -> str | None:
    blocked = set(SECTION_KEYWORDS)
    for line in lines[:12]:
        if phone and phone in line:
            continue
        if email and email in line:
            continue
        if any(keyword in line for keyword in blocked):
            continue
        cleaned = re.sub(r"[|｜].*", "", line).strip()
        if 2 <= len(cleaned) <= 8 and re.fullmatch(r"[\u4e00-\u9fa5·]{2,8}", cleaned):
            return cleaned
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
    patterns = [
        r"(\d+(?:\.\d+)?)\s*年(?:以上)?工作经验",
        r"工作经验[:：]?\s*(\d+(?:\.\d+)?)\s*年",
        r"(\d+(?:\.\d+)?)\s*年经验",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return Decimal(match.group(1))
    return None


def find_city(lines: list[str]) -> str | None:
    city_markers = ["现居", "所在地", "当前城市", "居住地", "城市"]
    for line in lines[:30]:
        if any(marker in line for marker in city_markers):
            match = re.search(r"(?:现居|所在地|当前城市|居住地|城市)[:： ]*([\u4e00-\u9fa5]{2,8})", line)
            if match:
                return match.group(1)
    return None


def find_expected_position(lines: list[str]) -> str | None:
    for line in lines:
        if "期望职位" in line or "求职意向" in line or "应聘职位" in line:
            value = re.sub(r".*?(期望职位|求职意向|应聘职位)[:： ]*", "", line).strip()
            return value[:120] if value else None
    return None


def find_current_work(lines: list[str]) -> tuple[str | None, str | None]:
    for index, line in enumerate(lines):
        if "工作经历" in line and index + 1 < len(lines):
            next_line = lines[index + 1]
            parts = re.split(r"[|｜]", next_line)
            company = parts[0].strip() if parts else None
            position = parts[1].strip() if len(parts) > 1 else None
            if company and len(company) <= 80:
                return company, position
    return None, None


def find_skills(text: str) -> list[str]:
    common_skills = [
        "Python", "Java", "C++", "C#", "JavaScript", "TypeScript", "React", "Vue", "SQL",
        "MySQL", "PostgreSQL", "Oracle", "Redis", "Docker", "Kubernetes", "Linux", "Excel",
        "Photoshop", "CAD", "SolidWorks", "PLC", "PMP", "销售", "运营", "招聘", "人事", "财务",
    ]
    lower_text = text.lower()
    found = []
    for skill in common_skills:
        if skill.lower() in lower_text:
            found.append(skill)
    return found

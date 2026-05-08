from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.storage.models import Candidate


CANDIDATE_COLUMNS = [
    "ID",
    "姓名",
    "性别",
    "年龄",
    "手机",
    "邮箱",
    "当前城市",
    "最高学历",
    "工作年限",
    "当前公司",
    "当前职位",
    "状态",
]


def export_candidates_excel(session: Session, include_plain_contact: bool = True) -> Path:
    settings = get_settings()
    candidates = session.query(Candidate).filter(Candidate.deleted_at.is_(None)).order_by(Candidate.id.desc()).all()
    rows = []
    for item in candidates:
        rows.append(
            {
                "ID": item.id,
                "姓名": item.name,
                "性别": item.gender,
                "年龄": item.age,
                "手机": item.phone_plain if include_plain_contact else item.phone_masked,
                "邮箱": item.email_plain if include_plain_contact else item.email_masked,
                "当前城市": item.current_city,
                "最高学历": item.highest_degree,
                "工作年限": item.years_of_experience,
                "当前公司": item.current_company,
                "当前职位": item.current_position,
                "状态": item.status,
            }
        )
    df = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    output_path = settings.export_dir / f"候选人导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)
    return output_path

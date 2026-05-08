from pathlib import Path

from docx import Document


def extract_docx_text(file_path: str | Path) -> str:
    document = Document(str(file_path))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(paragraphs)

"""Install agent-rules-books mini variants as Claude Code skills."""
import os
import re
import shutil
from pathlib import Path

SRC = Path("C:/Users/WIN/AppData/Local/Temp/agent-rules-books")
DST = Path.home() / ".claude" / "skills"

BOOKS = [
    "a-philosophy-of-software-design",
    "clean-architecture",
    "clean-code",
    "code-complete",
    "designing-data-intensive-applications",
    "domain-driven-design",
    "domain-driven-design-distilled",
    "implementing-domain-driven-design",
    "patterns-of-enterprise-application-architecture",
    "refactoring",
    "refactoring-guru",
    "release-it",
    "the-pragmatic-programmer",
    "working-effectively-with-legacy-code",
]


def extract_when_to_use(text: str) -> str:
    m = re.search(r"## When to use\s*\n+(.+?)(?:\n\n|\n##)", text, flags=re.DOTALL)
    if not m:
        return ""
    return " ".join(m.group(1).strip().split())


def extract_title(text: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    for book in BOOKS:
        mini = SRC / book / f"{book}.mini.md"
        if not mini.exists():
            print(f"skip {book}: no mini file")
            continue
        body = mini.read_text(encoding="utf-8")
        title = extract_title(body)
        when = extract_when_to_use(body)
        skill_name = book  # already kebab-case
        description = f"{title}. {when} Apply when the user asks for guidance, review, or refactoring inspired by this book."
        skill_dir = DST / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        frontmatter = f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
        skill_md.write_text(frontmatter + body, encoding="utf-8")
        print(f"installed: {skill_name}")


if __name__ == "__main__":
    main()

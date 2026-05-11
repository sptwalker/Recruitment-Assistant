import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recruitment_assistant.storage.db import init_database


def main() -> None:
    init_database()
    print("Database tables created or already exist.")


if __name__ == "__main__":
    main()

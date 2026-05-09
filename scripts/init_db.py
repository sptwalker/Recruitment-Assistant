import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recruitment_assistant.storage.db import Base, engine
from recruitment_assistant.storage import models  # noqa: F401


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("Database tables created or already exist.")


if __name__ == "__main__":
    main()

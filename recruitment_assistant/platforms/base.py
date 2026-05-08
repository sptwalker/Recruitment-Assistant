from abc import ABC, abstractmethod


class BasePlatformAdapter(ABC):
    platform_code: str

    @abstractmethod
    def login(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_logged_in(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_resume_list(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def fetch_resume_detail(self, resume_id: str) -> dict:
        raise NotImplementedError

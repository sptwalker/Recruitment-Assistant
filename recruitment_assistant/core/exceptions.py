class RecruitmentAssistantError(Exception):
    """Base application exception."""


class LoginStateExpiredError(RecruitmentAssistantError):
    """Raised when platform login state is invalid or expired."""


class PlatformUnsupportedError(RecruitmentAssistantError):
    """Raised when a platform adapter is not implemented."""

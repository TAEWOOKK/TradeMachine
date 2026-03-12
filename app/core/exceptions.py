from __future__ import annotations


class KisApiError(Exception):
    def __init__(self, msg_cd: str, msg: str) -> None:
        self.msg_cd = msg_cd
        super().__init__(f"[{msg_cd}] {msg}")


class TokenExpiredError(KisApiError):
    pass


class RateLimitError(KisApiError):
    pass

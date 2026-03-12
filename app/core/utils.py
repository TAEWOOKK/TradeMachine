from __future__ import annotations


def safe_int(val: str, default: int = 0) -> int:
    try:
        return int(val.replace(",", ""))
    except (ValueError, TypeError, AttributeError):
        return default


def safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val.replace(",", ""))
    except (ValueError, TypeError, AttributeError):
        return default

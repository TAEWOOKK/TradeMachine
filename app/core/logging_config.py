from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    Path("logs").mkdir(exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    )

    app_handler = RotatingFileHandler(
        "logs/app.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)

    trade_handler = RotatingFileHandler(
        "logs/trading.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    trade_handler.setLevel(logging.INFO)
    trade_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        "logs/error.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(app_handler)
    root.addHandler(error_handler)

    trade_logger = logging.getLogger("trading")
    trade_logger.addHandler(trade_handler)

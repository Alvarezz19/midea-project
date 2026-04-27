from __future__ import annotations

import logging

from app.core.config import settings


def configure_logging() -> None:
    """配置应用日志；后续可替换为结构化 JSON 日志。"""

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

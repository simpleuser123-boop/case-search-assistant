"""结构化日志配置。

安全红线（Day0 §7）：日志只记录阶段耗时与脱敏事件，
绝不含原始 query 或密钥值。本模块只做最小初始化。
"""
from __future__ import annotations

import logging

from app.core.config import settings


def setup_logging() -> logging.Logger:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("case_search")


logger = setup_logging()

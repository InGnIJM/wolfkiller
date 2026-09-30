"""服务端可观测性：公开回放承载不了的记录。

回放构建器过去用一句 `return []` 丢掉白名单外的取值，循环里的那几处还会把同批**所有**
普通记录一起丢掉（实测 `[poison, charm]` 两死产出 0 条事件），而且什么都不记，唯一的
症状是回放里悄悄少了一条。丢弃本身仍然不进公开 DTO —— 这里是它变得可见的地方。

诊断接口读这个计数（`api/routes/diagnostics_routes.py`），计数只在进程内、只记
`operation:code` 与次数，不带对局内容。
"""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

#: `"{operation}:{code}"` → 次数。
ROWS: Counter[str] = Counter()


def drop(operation: str, code: str, value: object = None) -> None:
    """Count and report one row or record the public contract cannot carry.

    ``value`` 只进服务端日志（排障要看是哪个取值），**不进**诊断响应。
    """
    ROWS[f"{operation}:{code}"] += 1
    logger.warning(
        "Dropped public replay row: operation=%s code=%s value=%r",
        operation, code, value,
    )

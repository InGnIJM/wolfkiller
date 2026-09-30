"""内部诊断接口：只读、本机可访问、不含对局数据。

服务本身有 `LocalAccessMiddleware`（仅本机），所以这里不需要额外鉴权；响应只暴露
`operation:code` 与次数，用于回答「这局回放为什么少了几条」。
"""
from fastapi import APIRouter

from app.api import replay_diagnostics

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/replay-drops")
def replay_drops() -> dict[str, dict[str, int]]:
    """公开回放丢弃的 `operation:code` 计数（进程内累计）。"""
    return {"drops": dict(sorted(replay_diagnostics.ROWS.items()))}

"""工作台接口：清单、归属与可写口径全部由服务端实时给出。

前端每次进入工作台都先 /api/auth/bootstrap 重新取一次归属，再拉本清单——
权限变更（转组/撤权）后缓存的旧任务一律以本清单重新判定，不沿用本地残留。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers.deps import require_session
from app.services.access import access

router = APIRouter(prefix="/api/workbench", tags=["工作台"])


@router.get("")
def my_tasks(session_pair=Depends(require_session)) -> dict:
    """当前账号在本单位可见的水文观测任务（含只读账号），每条带实时归属与可写标记。"""
    account, session = session_pair
    payload = access.workbench_for(account)
    payload["token_epoch"] = session.epoch
    return payload

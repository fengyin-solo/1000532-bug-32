"""水文地质接口：维护水文观测点，覆盖记录观测、标记异常、复核数据、观测类型转组。

鉴权口径：
- 读（列表/详情）按当前账号同单位过滤；
- 写（登记/动作/转组）必须带有效会话票据：只读拒绝、跨单位拒绝、权限变更后的旧票据 409。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.iam import AuthError, clock
from app.routers.deps import optional_session, raise_for_auth, require_session
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.access import access
from app.services.hydro import HydroService

router = APIRouter(prefix="/api/hydro", tags=["水文地质"])

service = HydroService()

LIST_FIELDS = ["观测编号", "观测类型", "所在钻孔", "静止水位", "降深", "出水量", "观测日期", "观测状态"]
STATUSES = ["待观测", "已观测", "数据异常", "已复核"]


class TransferPayload(BaseModel):
    target_group: str
    reason: str = "观测类型转组"
    op_time: int | None = None


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按观测编号检索"),
    status: str | None = Query(default=None, description="待观测、已观测、数据异常、已复核"),
    page: int = 1,
    size: int = 20,
    session_pair=Depends(optional_session),
) -> PageResult[dict]:
    """按观测编号与状态过滤水文观测点；列表归属以当前项目组为准，按账号同单位过滤。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    account = session_pair[0] if session_pair else None
    items, total = service.list_entries(account=account, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, session_pair=Depends(optional_session)) -> dict[str, Any]:
    """观测详情：归属与「当前账号可写」由服务端实时给出（与工作台、钻孔引用同源）。"""
    account = session_pair[0] if session_pair else None
    if account is None:
        raise HTTPException(status_code=401, detail="请先进入工作台建立会话")
    try:
        return access.detail_for(account, entry_id)
    except AuthError as error:
        raise_for_auth(error)
    raise HTTPException(status_code=404, detail=f"水文观测点 {entry_id} 不存在或已归档")  # pragma: no cover


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload, session_pair=Depends(require_session)) -> ActionResult:
    """登记水文观测点：归属以观测类型当前项目组为准，跨单位登记拒绝。"""
    account, _ = session_pair
    try:
        entry, missing = service.create_entry(payload.values, account=account)
    except AuthError as error:
        raise_for_auth(error)
        missing = []
        entry = None
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="水文观测点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload, session_pair=Depends(require_session)) -> ActionResult:
    """记录观测、标记异常、复核数据：只读账号拒绝、跨单位拒绝、旧会话拒绝。"""
    account, _ = session_pair
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, account=account, now=clock.now())
    except AuthError as error:
        raise_for_auth(error)
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/transfer", response_model=ActionResult)
def transfer_entry(entry_id: int, payload: TransferPayload, session_pair=Depends(require_session)) -> ActionResult:
    """观测类型转组：原子更新台账/工作台/钻孔引用三处口径，旧会话随权限版本一并失效。"""
    actor, _ = session_pair
    op_time = payload.op_time if payload.op_time is not None else clock.now()
    try:
        entry = access.transfer(
            entry_id,
            payload.target_group,
            actor=actor,
            op_time=op_time,
            reason=payload.reason,
        )
    except AuthError as error:
        raise_for_auth(error)
        entry = None
    return ActionResult(ok=True, message=f"观测点已转至「{entry['项目组']}」，三处台账已同步", entry=entry)


@router.get("/{entry_id}/audit", response_model=dict)
def entry_audit(entry_id: int) -> dict[str, Any]:
    """审计台账：历史观测记录按原组审计基准保留，可按点查询。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"水文观测点 {entry_id} 不存在或已归档")
    return {"entry_id": entry_id, "items": access.audit_trail(entry_id)}


@router.get("/export/all")
def export_entries(session_pair=Depends(optional_session)) -> dict[str, Any]:
    """导出水文地质清单：按当前账号同单位口径返回。"""
    account = session_pair[0] if session_pair else None
    items, total = service.list_entries(account=account, page=1, size=10000)
    return {"module": "hydro", "total": total, "items": items}

"""水文地质接口：维护水文观测点，覆盖记录观测、标记异常、复核数据等动作。

授权链路由 app.authz 统一把关：只读账号不能改动、跨单位/非本组拒绝、
旧授权版本会话不得提交。本路由的列表/明细同样走授权投影，与工作台、
钻孔引用页保持同一口径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from app.authz import AuthzError, authz
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.hydro import HydroService

router = APIRouter(prefix="/api/hydro", tags=["水文地质"])

service = HydroService()

LIST_FIELDS = ["观测编号", "观测类型", "所在钻孔", "静止水位", "降深", "出水量", "观测日期", "观测状态"]
STATUSES = ["待观测", "已观测", "数据异常", "已复核"]


def _token(x_auth_token: str | None) -> str:
    return (x_auth_token or "").strip()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按观测编号检索"),
    status: str | None = Query(default=None, description="待观测、已观测、数据异常、已复核"),
    page: int = 1,
    size: int = 20,
    x_auth_token: str | None = Header(default=None),
) -> PageResult[dict]:
    """按观测编号与状态过滤本单位可见的水文观测点；没有数据时返回空页。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    try:
        items = authz.ledger(_token(x_auth_token))["items"]
    except AuthzError as error:
        raise HTTPException(
            status_code=error.status, detail={"code": error.code, "message": error.message}
        ) from error
    if keyword:
        items = [row for row in items if keyword in str(row.get("观测编号", ""))]
    if status:
        items = [row for row in items if row.get("status") == status]
    total = len(items)
    start = max(page - 1, 0) * size
    return PageResult(items=items[start:start + size], total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, x_auth_token: str | None = Header(default=None)) -> dict:
    """读取单条水文观测点明细；跨单位/不存在统一给出可读说明。"""
    try:
        return authz.detail(_token(x_auth_token), entry_id)
    except AuthzError as error:
        raise HTTPException(
            status_code=error.status, detail={"code": error.code, "message": error.message}
        ) from error


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    x_auth_token: str | None = Header(default=None),
) -> ActionResult:
    """记录观测、标记异常、复核数据：授权不通过时明确拒绝并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry = authz.submit_action(_token(x_auth_token), entry_id, action)
    except AuthzError as error:
        raise HTTPException(
            status_code=error.status, detail={"code": error.code, "message": error.message}
        ) from error
    return ActionResult(ok=True, message=f"水文观测点已{action}", entry=entry)


@router.get("/export/all")
def export_entries(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """导出本单位可见的水文清单：口径与台账一致。"""
    try:
        ledger = authz.ledger(_token(x_auth_token))
    except AuthzError as error:
        raise HTTPException(
            status_code=error.status, detail={"code": error.code, "message": error.message}
        ) from error
    return {"module": "hydro", "total": ledger["total"], "items": ledger["items"]}

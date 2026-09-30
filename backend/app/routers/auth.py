"""账号与会话接口：进入工作台前引导会话、查询当前账号口径、管理员撤权。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.iam import ACCOUNTS, Account, AuthError, GROUPS, clock, iam
from app.routers.deps import raise_for_auth, require_session
from app.schemas import ActionResult

router = APIRouter(prefix="/api/auth", tags=["账号与授权"])


class BootstrapPayload(BaseModel):
    account_id: str


class RevokePayload(BaseModel):
    account_id: str
    reason: str = "权限调整"
    op_time: int | None = None  # 不传则取服务端操作时间；测试可指定以验证并发定序


def _account_view(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "名称": account.名称,
        "单位": account.单位,
        "角色": account.角色,
        "只读": account.角色 == "viewer",
        "项目组": [
            {"id": gid, "名称": GROUPS[gid]["名称"], "单位": GROUPS[gid]["单位"]}
            for gid in account.项目组
            if gid in GROUPS
        ],
        "active": iam.is_active(account.id, 2**62),
    }


@router.get("/groups")
def list_groups() -> dict[str, Any]:
    """全量项目组目录（转组候选、归属名称展示用）。"""
    return {"items": snapshot_groups()}


@router.get("/accounts")
def list_accounts() -> dict[str, Any]:
    """可选账号目录（演示用登录切换）。"""
    return {"items": [_account_view(account) for account in ACCOUNTS.values()]}


@router.post("/bootstrap")
def bootstrap(payload: BootstrapPayload) -> dict[str, Any]:
    """进入工作台时重新引导：重新取一次归属，签发携带当前权限版本的会话票据。

    转组/撤权后旧票据全部失效，前端必须重新调本接口（重新进入工作台）才能再提交。
    """
    try:
        session = iam.bootstrap(payload.account_id, now=clock.now())
    except AuthError as error:
        raise_for_auth(error)
    account = iam.account(session.account_id)
    return {"token": session.token, "auth_epoch": session.epoch, "account": _account_view(account)}


@router.get("/me")
def me(session_pair=Depends(require_session)) -> dict[str, Any]:
    account, session = session_pair
    view = _account_view(account)
    view["auth_epoch"] = session.epoch
    return view


@router.post("/revoke", response_model=ActionResult)
def revoke(payload: RevokePayload, session_pair=Depends(require_session)) -> ActionResult:
    """撤权：仅平台管理员；与转组同口径按操作时间定序，先到的生效并原子失效旧会话。"""
    actor, _ = session_pair
    op_time = payload.op_time if payload.op_time is not None else clock.now()
    try:
        event = iam.revoke(payload.account_id, op_time=op_time, actor=actor, reason=payload.reason)
    except AuthError as error:
        raise_for_auth(error)
    target = iam.account(payload.account_id)
    return ActionResult(
        ok=True,
        message=f"{target.名称} 已撤权（t={event.op_time}），其旧会话已全部失效",
        entry={"auth_epoch": iam.auth_epoch},
    )

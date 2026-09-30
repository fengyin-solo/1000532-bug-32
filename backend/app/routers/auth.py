"""授权与水文归属接口：登录、工作台、观测台账、钻孔引用、转组与撤权。

这一组接口是「观测类型转组」授权与失效链路的 HTTP 出口；授权规则、原子性、
定序与口径统一都在 app.authz 里实现，路由层只负责取会话令牌与错误翻译。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.authz import AuthzError, authz

router = APIRouter(prefix="/api/auth", tags=["授权与水文归属"])


class LoginPayload(BaseModel):
    username: str


class TransferPayload(BaseModel):
    obs_type: str = Field(description="要转组的观测类型")
    to_group: str = Field(description="目标项目组 id")
    op_time: str | None = Field(default=None, description="操作时间，用于并发定序；缺省取服务端当前时间")


class RevokePayload(BaseModel):
    username: str
    group_id: str
    op_time: str | None = None


class GrantPayload(BaseModel):
    username: str
    group_id: str


def _token(x_auth_token: str | None) -> str:
    return (x_auth_token or "").strip()


def _fail(error: AuthzError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"code": error.code, "message": error.message})


@router.post("/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    """登录：返回会话令牌与当前授权版本，前端据此拉取工作台。"""
    try:
        session = authz.login(payload.username.strip())
    except AuthzError as error:
        raise _fail(error) from error
    return {
        "token": session.token,
        "username": session.username,
        "unit": session.unit,
        "role": session.role,
        "auth_version": session.auth_version,
    }


@router.post("/logout")
def logout(x_auth_token: str | None = Header(default=None)) -> dict[str, bool]:
    """退出登录：服务端立即作废会话，缓存任务不可再用。"""
    authz.logout(_token(x_auth_token))
    return {"ok": True}


@router.get("/workbench")
def workbench(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """工作台清单：重新进入时重新取一次归属，并刷新会话授权版本。"""
    try:
        return authz.workbench(_token(x_auth_token))
    except AuthzError as error:
        raise _fail(error) from error


@router.get("/hydro-ledger")
def hydro_ledger(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """观测台账：与工作台、钻孔引用同口径的只读清单。"""
    try:
        return authz.ledger(_token(x_auth_token))
    except AuthzError as error:
        raise _fail(error) from error


@router.get("/hydro-points/{point_id}")
def hydro_point(point_id: int, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """观测详情：以服务端当前归属为准，跨单位访问按不存在处理。"""
    try:
        return authz.detail(_token(x_auth_token), point_id)
    except AuthzError as error:
        raise _fail(error) from error


@router.get("/borehole-refs/{borehole_code}")
def borehole_refs(borehole_code: str, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """钻孔引用页：回填该钻孔的共享水文引用，归属与台账保持一致。"""
    try:
        return authz.borehole_references(_token(x_auth_token), borehole_code)
    except AuthzError as error:
        raise _fail(error) from error


@router.post("/hydro-points/{point_id}/submit")
def submit_action(
    point_id: int,
    payload: dict[str, Any],
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """观测数据提交：只读账号、跨单位、非本组、旧会话一律拒绝。"""
    action = str(payload.get("action") or "").strip()
    values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
    try:
        view = authz.submit_action(_token(x_auth_token), point_id, action, values)
    except AuthzError as error:
        raise _fail(error) from error
    return {"ok": True, "message": f"水文观测点已{action}", "entry": view}


@router.post("/transfer")
def transfer_type(
    payload: TransferPayload,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """观测类型转组：权限更新与缓存失效原子落地，按操作时间与撤权一起定序。"""
    try:
        return authz.transfer_type(
            _token(x_auth_token), payload.obs_type.strip(), payload.to_group.strip(), payload.op_time
        )
    except AuthzError as error:
        raise _fail(error) from error


@router.post("/revoke")
def revoke(
    payload: RevokePayload,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """并发撤权：与转组按操作时间在项目组维度定序，先到者生效。"""
    try:
        return authz.revoke(_token(x_auth_token), payload.username.strip(), payload.group_id.strip(), payload.op_time)
    except AuthzError as error:
        raise _fail(error) from error


@router.post("/grant")
def grant(
    payload: GrantPayload,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """授权入组：演示环境用于恢复组员关系。"""
    try:
        return authz.grant(_token(x_auth_token), payload.username.strip(), payload.group_id.strip())
    except AuthzError as error:
        raise _fail(error) from error


@router.get("/audit")
def audit(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """审计流：转组、撤权、补数、提交留痕，历史记录按原组基准保留。"""
    try:
        items = authz.list_audit(_token(x_auth_token))
    except AuthzError as error:
        raise _fail(error) from error
    return {"total": len(items), "items": items}

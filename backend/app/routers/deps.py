"""接口层共享依赖：从会话票据解析当前账号，统一映射授权错误为 HTTP 状态。"""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.iam import Account, AuthError, SessionInfo, iam


_STATUS_BY_CODE = {
    "no_session": (401, "未建立会话"),
    "stale_session": (409, "会话已因权限变更失效"),
    "revoked": (403, "账号已撤权"),
    "readonly": (403, "只读账号无权写操作"),
    "cross_unit": (403, "跨单位操作被拒绝"),
    "not_my_group": (409, "归属已变更，请刷新工作台"),
    "forbidden": (403, "无权执行该操作"),
    "bad_target": (400, "目标不合法"),
    "bad_ownership": (400, "无法确定归属"),
    "conflict": (409, "操作冲突，先到的操作已生效"),
    "no_change": (400, "无需变更"),
    "not_found": (404, "记录不存在"),
}


def raise_for_auth(error: AuthError) -> None:
    status, default = _STATUS_BY_CODE.get(error.code, (403, "授权失败"))
    raise HTTPException(status_code=status, detail=error.message or default)


def run_guarded(func):
    """执行会抛 AuthError 的业务调用，把授权错误统一翻译成 HTTP 异常。"""
    try:
        return func()
    except AuthError as error:
        raise_for_auth(error)


def require_session(x_session_token: str | None = Header(default=None, alias="X-Session-Token")) -> tuple[Account, SessionInfo]:
    """写操作依赖：票据缺失/被注销/epoch 落后全部拒绝（fail-closed）。"""
    try:
        return iam.authenticate(x_session_token)
    except AuthError as error:
        raise_for_auth(error)
        raise  # pragma: no cover - raise_for_auth 必抛


def optional_session(x_session_token: str | None = Header(default=None, alias="X-Session-Token")):
    if not x_session_token:
        return None
    # 读接口允许票据失效：前端退化为未登录口径，而不是报错；写接口走 require_session
    try:
        return iam.authenticate(x_session_token)
    except AuthError:
        return None

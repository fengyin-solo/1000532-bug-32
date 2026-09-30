"""身份与授权基座：单位、项目组、账号、观测类型归属、授权时间线与会话注册表。

口径约定（与业务修复一一对应）：
- 水文观测点的归属以「当前项目组」为准；项目组隶属单位，跨单位提交一律拒绝。
- 角色分 editor（可改本组观测数据）、viewer（只读，不能改动观测数据）、admin（平台管理员，可撤权/转组）。
- 撤权与转组都按操作时间（op_time）定序：只有先到的生效，迟到的冲突操作拒绝。
- 全局权限版本号 auth_epoch 与会话注册表共同实现「缓存失效与权限更新原子落地」：
  任何转组/撤权先抬 epoch，旧会话携带的 epoch 立刻对不上，写操作 fail-closed 拒绝。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable


class Clock:
    """操作时间源：默认单调纳秒，测试可替换以确定性地验证并发定序。"""

    def now(self) -> int:
        return time.monotonic_ns()


clock = Clock()

# ---------------------------------------------------------------- 组织目录

UNIT_A = "地质勘查一处"
UNIT_B = "地质勘查二处"

GROUPS: dict[str, dict[str, str]] = {
    # group_id -> {名称, 单位}
    "A-HYDRO": {"名称": "一处水文组", "单位": UNIT_A},
    "A-PUMP": {"名称": "一处抽水试验组", "单位": UNIT_A},
    "A-DRILL": {"名称": "一处钻探组", "单位": UNIT_A},
    "B-HYDRO": {"名称": "二处水文组", "单位": UNIT_B},
    "B-DRILL": {"名称": "二处钻探组", "单位": UNIT_B},
}

# 观测类型 -> 项目组（归属以观测类型当前配置的组为准，改这里即完成「观测类型转组」）
TYPE_TO_GROUP: dict[str, str] = {
    "稳定水位观测": "A-HYDRO",
    "钻孔简易水文": "A-DRILL",
    "抽水试验": "A-PUMP",
    "流量观测": "B-HYDRO",
    "钻孔涌水观测": "B-DRILL",
}

# 钻孔 -> 项目组：钻孔资料（含共享引用页）的属主
BOREHOLE_TO_GROUP: dict[str, str] = {
    "BORE-0001": "A-DRILL",
    "BORE-0002": "A-PUMP",
    "BORE-0003": "A-DRILL",
}

ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLE_ADMIN = "admin"


@dataclass(frozen=True)
class Account:
    id: str
    名称: str
    单位: str
    角色: str
    项目组: tuple[str, ...]


ACCOUNTS: dict[str, Account] = {
    "a-editor": Account("a-editor", "一处水文组-经办人", UNIT_A, ROLE_EDITOR, ("A-HYDRO",)),
    "a-pump": Account("a-pump", "一处抽水组-经办人", UNIT_A, ROLE_EDITOR, ("A-PUMP",)),
    "a-drill": Account("a-drill", "一处钻探组-经办人", UNIT_A, ROLE_EDITOR, ("A-DRILL",)),
    "a-viewer": Account("a-viewer", "一处水文组-只读账号", UNIT_A, ROLE_VIEWER, ("A-HYDRO",)),
    "b-editor": Account("b-editor", "二处水文组-经办人", UNIT_B, ROLE_EDITOR, ("B-HYDRO",)),
    "platform-admin": Account("platform-admin", "平台管理员", "平台", ROLE_ADMIN, ()),
}


# ---------------------------------------------------------------- 授权时间线

@dataclass
class GrantEvent:
    """授权变更事件：revoke=True 表示撤权，False 表示授权。时间线上只增不改。"""

    op_time: int
    seq: int
    account_id: str
    revoke: bool
    group_id: str | None
    actor: str
    原因: str


@dataclass
class SessionInfo:
    token: str
    account_id: str
    epoch: int


class AuthError(Exception):
    """授权失败：code 与提示一并返回给接口层映射 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class IAM:
    """账号目录 + 授权时间线 + 会话注册表，全部状态都在同一把锁下读写。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[GrantEvent] = []
        self._seq = 0
        self._auth_epoch = 1
        self._sessions: dict[str, SessionInfo] = {}
        self._latest_token: dict[str, str] = {}

    # ------------------------------------------------------------ 读

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def auth_epoch(self) -> int:
        return self._auth_epoch

    def account(self, account_id: str | None) -> Account:
        account = ACCOUNTS.get(account_id or "")
        if account is None:
            raise AuthError("no_session", "未选择值班账号，请重新进入工作台")
        return account

    def group(self, group_id: str) -> dict[str, str]:
        group = GROUPS.get(group_id)
        if group is None:
            raise AuthError("bad_target", f"目标项目组 {group_id} 不存在")
        return group

    def group_of_type(self, obs_type: str) -> str | None:
        return TYPE_TO_GROUP.get((obs_type or "").strip())

    def group_of_borehole(self, borehole_no: str) -> str | None:
        return BOREHOLE_TO_GROUP.get((borehole_no or "").strip())

    def is_revoked_at(self, account_id: str, op_time: int) -> bool:
        """截至 op_time（含）最近一次事件是否为撤权——并发撤权按操作时间定序的判定核心。"""
        latest: GrantEvent | None = None
        for event in self._events:
            if event.account_id == account_id and event.op_time <= op_time:
                if latest is None or (event.op_time, event.seq) > (latest.op_time, latest.seq):
                    latest = event
        return bool(latest and latest.revoke)

    def is_active(self, account_id: str, now: int) -> bool:
        return not self.is_revoked_at(account_id, now)

    def role_can_write(self, account: Account) -> bool:
        return account.角色 in (ROLE_EDITOR, ROLE_ADMIN)

    def can_manage_group(self, account: Account, group_id: str) -> bool:
        """账号是否能以经办人身份动这个组的数据：同单位且在组内（管理员走 admin 通道另判）。"""
        target = self.group(group_id)
        if account.单位 != target["单位"]:
            return False
        return group_id in account.项目组

    # ------------------------------------------------------------ 写：授权变更（原子）

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def bump_epoch_locked(self) -> int:
        """抬升权限版本号并注销全部会话。必须在 self._lock 内调用。"""
        self._auth_epoch += 1
        self._sessions.clear()
        self._latest_token.clear()
        return self._auth_epoch

    def revoke(self, account_id: str, *, op_time: int, actor: Account, reason: str) -> GrantEvent:
        """撤权：迟到者（已有不晚于它的撤权生效）拒绝，先到的生效并原子失效全部旧会话。"""
        with self._lock:
            target = self.account(account_id)
            if actor.角色 != ROLE_ADMIN:
                raise AuthError("forbidden", "只有平台管理员可以撤权")
            if target.角色 == ROLE_ADMIN:
                raise AuthError("forbidden", "平台管理员账号不可撤权")
            # 操作时间定序：并发撤权只有先到的生效。
            # - 到达时（now）账号已被更早到达的撤权置为撤权态 → 本次迟到，拒绝；
            # - 时间线上已有 op_time <= 本次 op_time 的撤权 → 同操作时间维度的重复，拒绝。
            if self.is_revoked_at(account_id, clock.now()):
                raise AuthError(
                    "conflict",
                    f"撤权冲突：{target.名称} 已被先到的撤权操作置为撤权态",
                )
            for event in self._events:
                if event.account_id == account_id and event.revoke and event.op_time <= op_time:
                    raise AuthError(
                        "conflict",
                        f"撤权冲突：{target.名称} 已在 t={event.op_time} 被撤权，先到的操作生效",
                    )
            event = GrantEvent(
                op_time=op_time,
                seq=self._next_seq(),
                account_id=account_id,
                revoke=True,
                group_id=None,
                actor=actor.id,
                原因=reason,
            )
            self._events.append(event)
            self.bump_epoch_locked()  # 先失效、再提交语义：旧会话一律不能再用
            return event

    def restore(self, account_id: str, *, op_time: int, actor: Account, reason: str) -> GrantEvent:
        """重新授权（测试/恢复用）：同样按操作时间排序并失效会话。"""
        with self._lock:
            target = self.account(account_id)
            if actor.角色 != ROLE_ADMIN:
                raise AuthError("forbidden", "只有平台管理员可以重新授权")
            event = GrantEvent(
                op_time=op_time,
                seq=self._next_seq(),
                account_id=account_id,
                revoke=False,
                group_id=None,
                actor=actor.id,
                原因=reason,
            )
            self._events.append(event)
            self.bump_epoch_locked()
            return event

    def reset(self) -> None:
        """恢复初始授权状态（测试隔离用，业务路径不调用）。"""
        with self._lock:
            self._events.clear()
            self._seq = 0
            self._auth_epoch = 1
            self._sessions.clear()
            self._latest_token.clear()

    def events(self, account_id: str | None = None) -> list[GrantEvent]:
        with self._lock:
            return [e for e in self._events if account_id is None or e.account_id == account_id]

    # ------------------------------------------------------------ 会话

    def bootstrap(self, account_id: str, *, now: int) -> SessionInfo:
        """引导会话：已撤权账号不给会话；每次引导都重新取一次归属并签发携带当前 epoch 的票据。"""
        with self._lock:
            account = self.account(account_id)
            if self.is_revoked_at(account_id, now):
                raise AuthError("revoked", f"{account.名称} 已被撤权，不能进入工作台")
            old = self._latest_token.get(account_id)
            if old:
                self._sessions.pop(old, None)
            token = uuid.uuid4().hex
            session = SessionInfo(token=token, account_id=account_id, epoch=self._auth_epoch)
            self._sessions[token] = session
            self._latest_token[account_id] = token
            return session

    def authenticate(self, token: str | None) -> tuple[Account, SessionInfo]:
        """校验写请求票据：无票据/已注销/epoch 落后（权限已更新）一律拒绝——fail-closed。"""
        with self._lock:
            if not token:
                raise AuthError("no_session", "会话缺失或已失效，请重新进入工作台")
            session = self._sessions.get(token)
            if session is None:
                raise AuthError("stale_session", "登录态已被权限变更注销，请重新进入工作台")
            if session.epoch != self._auth_epoch:
                # 正常路径下注销时会话会被清掉；保留 epoch 双检，任何漏网旧票据都不放行
                self._sessions.pop(token, None)
                raise AuthError("stale_session", "权限已更新，旧会话不得继续提交，请重新进入工作台")
            return self.account(session.account_id), session


# 进程内单例：与内存仓库同一生命周期
iam = IAM()


def snapshot_groups() -> list[dict[str, Any]]:
    """给工作台/接口返回项目组目录快照。"""
    return [
        {"id": gid, "名称": meta["名称"], "单位": meta["单位"]}
        for gid, meta in GROUPS.items()
    ]


def visible_group_ids(account: Account) -> Iterable[str]:
    """账号可见的项目组：本单位全部组 + 平台管理员全量。读不拦跨组（共享口径），写另判。"""
    if account.角色 == ROLE_ADMIN:
        return tuple(GROUPS)
    return tuple(gid for gid, meta in GROUPS.items() if meta["单位"] == account.单位)

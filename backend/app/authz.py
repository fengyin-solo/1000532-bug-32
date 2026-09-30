"""水文观测点授权域：项目组归属、会话与缓存失效、转组/撤权的原子定序。

口径约定（工作台清单、观测台账、钻孔引用页三处共用本模块作为唯一事实源）：

* 归属以观测类型当前映射的项目组为准；观测类型转组后，该类型下全部观测点
  归属立即跟随，历史观测记录仍按原组审计基准保留。
* 会话携带授权版本号；任何权限更新都会推进版本号，版本号落后的旧会话一律
  不得再提交，必须重新进入工作台拉取最新归属。
* 转组与撤权在同一把锁内完成「权限更新 + 缓存失效」，先做整表快照，任何一步
  失败都整体回滚——落地不成功时，旧会话仍按旧权限工作，绝不出现半生效状态。
* 并发撤权与转组按操作时间（op_time）在受影响项目组上定序，先到者生效，
  后到且时间戳更旧的请求判定为陈旧操作直接拒绝。
"""
from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.store import store

HYDRO_MODULE = "hydro"

# ---- 单位、项目组、账号的固定名册 -------------------------------------------------

UNITS: dict[str, str] = {
    "A": "地质一大队",
    "B": "地质二大队",
}

# group_id -> 项目组信息；membership 记录组员及其是否在组（撤权即置 False）
GROUPS: dict[str, dict[str, Any]] = {
    "G-A1": {"name": "第一水文组", "unit": "A", "membership": {}},
    "G-A2": {"name": "第二水文组", "unit": "A", "membership": {}},
    "G-B1": {"name": "第三水文组", "unit": "B", "membership": {}},
}

# 存量无归属点做采集顺序补数时的兜底组
DEFAULT_GROUP = "G-A1"

# username -> 账号信息；role: admin / editor / viewer（只读）
USERS: dict[str, dict[str, str]] = {
    "admin_a": {"name": "一队管理员", "unit": "A", "role": "admin"},
    "editor_a1": {"name": "王观测", "unit": "A", "role": "editor"},
    "editor_a2": {"name": "李观测", "unit": "A", "role": "editor"},
    "viewer_a": {"name": "一队查阅员", "unit": "A", "role": "viewer"},
    "editor_b1": {"name": "赵观测", "unit": "B", "role": "editor"},
    "viewer_b": {"name": "二队查阅员", "unit": "B", "role": "viewer"},
}

# 观测类型 -> 当前所属项目组（可随转组操作变更）
INITIAL_TYPE_GROUPS: dict[str, str] = {
    "稳定流抽水试验": "G-A1",
    "非稳定流抽水试验": "G-A2",
    # 「水位长期观测」初始无映射：用于演示存量点按采集顺序补数
}


class AuthzError(Exception):
    """授权域业务错误：code 供前端区分失效原因。"""

    def __init__(self, code: str, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class Session:
    token: str
    username: str
    unit: str
    role: str
    auth_version: int
    created_at: str
    alive: bool = True


@dataclass
class _Snapshot:
    """一次权限变更涉及的全部可变状态，失败时整体回滚。"""

    type_groups: dict[str, str]
    points: list[dict[str, Any]]
    membership: dict[str, dict[str, bool]]
    group_ops: dict[str, str]
    auth_version: int
    audit: list[dict[str, Any]]


class AuthzDomain:
    """授权域单例：被路由层以依赖方式取用。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.type_groups: dict[str, str] = dict(INITIAL_TYPE_GROUPS)
        # 会话表 token -> Session
        self.sessions: dict[str, Session] = {}
        # 授权版本号：权限每变更一次 +1，旧会话据此失效
        self.auth_version = 1
        # 每个项目组最近一次权限变更（转组/撤权）的操作时间，用于并发定序
        self.group_ops: dict[str, str] = {gid: "" for gid in GROUPS}
        # 审计台账：转组、撤权、补数、观测提交全部留痕
        self.audit: list[dict[str, Any]] = []
        self._ownership_backfilled = False

    # ---- 基础查询 -----------------------------------------------------------------

    def reset(self) -> None:
        """恢复到初始授权态并重新补数：仅供测试在单例上清空运行态变更。"""
        with self._lock:
            for gid, group in GROUPS.items():
                group["membership"] = {}
            _seed_membership()
            self.type_groups = dict(INITIAL_TYPE_GROUPS)
            self.sessions = {}
            self.auth_version = 1
            self.group_ops = {gid: "" for gid in GROUPS}
            self.audit = []
            self._ownership_backfilled = False
            store.reset()
            self.backfill_ownership()

    def group_name(self, group_id: str) -> str:
        group = GROUPS.get(group_id)
        return group["name"] if group else group_id

    @staticmethod
    def user_view(username: str) -> dict[str, str]:
        return {"username": username, **USERS[username]}

    def _is_member(self, username: str, group_id: str) -> bool:
        return bool(GROUPS[group_id]["membership"].get(username))

    def can_write(self, user: dict[str, str], point: dict[str, Any]) -> bool:
        """只读账号不能改动观测数据；归属以当前项目组为准，跨单位一律拒绝。"""
        if user["role"] == "viewer":
            return False
        group_id = str(point.get("所属项目组"))
        if group_id not in GROUPS:
            return False
        # 归属以当前项目组对应的单位为准
        if GROUPS[group_id]["unit"] != user["unit"]:
            return False
        if user["role"] == "admin":
            return True
        return self._is_member(user["username"], group_id)

    # ---- 存量归属补数（按采集顺序） -------------------------------------------------

    def backfill_ownership(self) -> None:
        """启动后首次调用：给无归属的存量观测点按采集顺序（id 升序）补数。

        观测类型已有映射的按映射回填；没有映射的回补兜底组，并在审计中保留
        「采集顺序补数」的来源说明，保证台账可追溯。
        """
        with self._lock:
            if self._ownership_backfilled:
                return
            points = sorted(store.rows(HYDRO_MODULE), key=lambda row: int(row.get("id", 0)))
            for point in points:
                if point.get("所属项目组"):
                    continue
                obs_type = str(point.get("观测类型") or "")
                group_id = self.type_groups.get(obs_type)
                source = "类型映射"
                if group_id is None:
                    group_id = DEFAULT_GROUP
                    source = "采集顺序补数"
                self._stamp_owner(point, group_id, source)
                self._append_audit(
                    op="归属补数",
                    point=point,
                    group_to=group_id,
                    group_from=None,
                    operator="system",
                    detail=f"存量点按采集顺序补数（来源：{source}）",
                )
            self._ownership_backfilled = True

    def _stamp_owner(self, point: dict[str, Any], group_id: str, source: str) -> None:
        point["所属项目组"] = group_id
        point["所属单位"] = UNITS[GROUPS[group_id]["unit"]]
        point["归属来源"] = source

    # ---- 会话与缓存版本 ------------------------------------------------------------

    def login(self, username: str) -> Session:
        user = USERS.get(username)
        if user is None:
            raise AuthzError("unknown_user", f"账号 {username} 不存在", status=404)
        with self._lock:
            session = Session(
                token=uuid.uuid4().hex,
                username=username,
                unit=user["unit"],
                role=user["role"],
                auth_version=self.auth_version,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            self.sessions[session.token] = session
        return session

    def logout(self, token: str) -> None:
        with self._lock:
            session = self.sessions.pop(token, None)
            if session is not None:
                session.alive = False

    def _require_session(self, token: str) -> Session:
        if not token:
            raise AuthzError("unauthenticated", "未登录或会话已失效，请重新进入工作台", status=401)
        session = self.sessions.get(token)
        if session is None or not session.alive:
            raise AuthzError("session_gone", "登录态已失效，请重新进入工作台", status=401)
        return session

    def _require_fresh_session(self, token: str) -> Session:
        """写操作专用：会话的授权版本必须与当前一致，旧会话不得继续提交。"""
        session = self._require_session(token)
        if session.auth_version != self.auth_version:
            raise AuthzError(
                "permission_stale",
                "权限已更新，本地缓存的任务已失效，请重新进入工作台后再操作",
            )
        return session

    def refresh_session(self, session: Session) -> None:
        """重新进入工作台：把会话对齐到最新授权版本，重新取一次归属。"""
        with self._lock:
            if session.alive:
                session.auth_version = self.auth_version

    # ---- 读侧：工作台 / 台账 / 钻孔引用，三处同一口径 -------------------------------

    def _visible_points(self, user: dict[str, str]) -> list[dict[str, Any]]:
        """非本组/非本单位账号看不到对方的观测点（详情与引用同样适用）。"""
        rows = store.rows(HYDRO_MODULE)
        return [
            row
            for row in rows
            if (group_id := str(row.get("所属项目组") or ""))
            and group_id in GROUPS
            and GROUPS[group_id]["unit"] == user["unit"]
        ]

    def _point_view(self, user: dict[str, str], point: dict[str, Any]) -> dict[str, Any]:
        """所有出口对观测点的投影完全一致，保证三处口径统一。"""
        group_id = str(point.get("所属项目组"))
        return {
            "id": point.get("id"),
            "观测编号": point.get("观测编号"),
            "观测类型": point.get("观测类型"),
            "所在钻孔": point.get("所在钻孔"),
            "静止水位": point.get("静止水位"),
            "降深": point.get("降深"),
            "出水量": point.get("出水量"),
            "观测日期": point.get("观测日期"),
            "观测状态": point.get("观测状态"),
            "status": point.get("status"),
            "pending": point.get("pending"),
            "所属项目组": group_id,
            "所属项目组名称": self.group_name(group_id),
            "所属单位": point.get("所属单位"),
            "归属来源": point.get("归属来源"),
            "can_edit": self.can_write(user, point),
        }

    def workbench(self, token: str) -> dict[str, Any]:
        """工作台清单：每次进入都重新取一次归属，并把授权版本带回前端。"""
        with self._lock:
            session = self._require_session(token)
            # 重新进入工作台即重新对齐权限与归属，缓存的旧任务清单被本次结果覆盖
            self.refresh_session(session)
            user = self.user_view(session.username)
            items = [self._point_view(user, row) for row in self._visible_points(user)]
            items.sort(key=lambda row: int(row.get("id") or 0))
            return {
                "operator": {"username": session.username, **user},
                "auth_version": self.auth_version,
                "total": len(items),
                "items": items,
            }

    def ledger(self, token: str) -> dict[str, Any]:
        """观测台账：与工作台、钻孔引用共用同一投影，只做只读审计增强。"""
        with self._lock:
            session = self._require_session(token)
            user = self.user_view(session.username)
            items = [self._point_view(user, row) for row in self._visible_points(user)]
            items.sort(key=lambda row: int(row.get("id") or 0))
            return {"total": len(items), "items": items}

    def detail(self, token: str, point_id: int) -> dict[str, Any]:
        """观测详情：归属以服务端当前数据为准，不信任任何本地缓存。"""
        with self._lock:
            session = self._require_session(token)
            user = self.user_view(session.username)
            point = self._find_visible(point_id, user)
            view = self._point_view(user, point)
            view["history"] = list(point.get("history") or [])
            return view

    def borehole_references(self, token: str, borehole_code: str) -> dict[str, Any]:
        """钻孔引用页：回填该钻孔当前被哪些观测点共享引用，归属取最新值。"""
        with self._lock:
            session = self._require_session(token)
            user = self.user_view(session.username)
            referenced = [
                self._point_view(user, row)
                for row in self._visible_points(user)
                if str(row.get("所在钻孔") or "") == borehole_code
            ]
            return {
                "钻孔编号": borehole_code,
                "total": len(referenced),
                "items": referenced,
                "auth_version": self.auth_version,
            }

    def _find_visible(self, point_id: int, user: dict[str, str]) -> dict[str, Any]:
        for row in self._visible_points(user):
            if int(row.get("id", 0)) == point_id:
                return row
        # 不向跨单位账号暴露存在性：统一按不存在/已归档处理
        raise AuthzError(
            "not_found",
            f"水文观测点 {point_id} 不存在、已归档或不在本单位权限范围内",
            status=404,
        )

    # ---- 观测提交（旧会话 / 跨单位 / 只读 全部拦下） --------------------------------

    def submit_action(
        self,
        token: str,
        point_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._require_fresh_session(token)
            user = self.user_view(session.username)
            point = self._find_visible(point_id, user)
            if user["role"] == "viewer":
                raise AuthzError("readonly", "只读账号不能改动观测数据")
            if not self.can_write(user, point):
                # 归属已随转组变化：明确告知以当前项目组为准
                raise AuthzError(
                    "not_my_group",
                    "该观测点归属已变更：归属以当前项目组为准，跨单位提交一律拒绝",
                )
            # 复用水文服务的状态流转规则，但避免循环导入故局部引入
            from app.services.hydro import ACTION_RULES, STATUS_ORDER

            action = (action or "").strip()
            if action not in ACTION_RULES:
                raise AuthzError("bad_action", f"动作「{action}」不属于水文地质可执行范围", status=400)
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                raise AuthzError("bad_target", f"目标状态「{target}」不在允许的状态序列里", status=400)
            point["status"] = target
            point["pending"] = target != STATUS_ORDER[-1]
            point["abnormal"] = False
            point["观测状态"] = target
            group_id = str(point["所属项目组"])
            history = point.setdefault("history", [])
            history.append(
                {
                    "op": action,
                    "operator": session.username,
                    "operator_name": user["name"],
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "audit_group": group_id,  # 历史观测记录按提交时所在组留审计基准
                    "audit_group_name": self.group_name(group_id),
                    "values": values or {},
                }
            )
            self._append_audit(
                op=action,
                point=point,
                group_from=group_id,
                group_to=group_id,
                operator=session.username,
            )
            return self._point_view(user, point)

    # ---- 转组 / 撤权：原子落地 + 操作时间定序 ---------------------------------------

    def _snapshot(self) -> _Snapshot:
        return _Snapshot(
            type_groups=dict(self.type_groups),
            points=deepcopy(store.rows(HYDRO_MODULE)),
            membership={gid: dict(g["membership"]) for gid, g in GROUPS.items()},
            group_ops=dict(self.group_ops),
            auth_version=self.auth_version,
            audit=deepcopy(self.audit),
        )

    def _restore(self, snap: _Snapshot) -> None:
        self.type_groups = snap.type_groups
        current = store.rows(HYDRO_MODULE)
        current.clear()
        current.extend(snap.points)
        for gid, membership in snap.membership.items():
            GROUPS[gid]["membership"] = membership
        self.group_ops = snap.group_ops
        self.auth_version = snap.auth_version
        self.audit = snap.audit

    def _check_order(self, group_ids: list[str], op_time: str) -> None:
        for gid in group_ids:
            last = self.group_ops.get(gid, "")
            if last and op_time <= last:
                raise AuthzError(
                    "stale_op",
                    f"项目组 {self.group_name(gid)} 已存在更晚或同时到达的权限变更"
                    f"（{last}），本次操作（{op_time}）按操作时间定序不予生效",
                    status=409,
                )

    def transfer_type(
        self,
        token: str,
        obs_type: str,
        to_group: str,
        op_time: str | None = None,
    ) -> dict[str, Any]:
        """观测类型转组：归属更新、三处回写、缓存失效在同一临界区原子完成。"""
        with self._lock:
            session = self._require_fresh_session(token)
            admin = self.user_view(session.username)
            if admin["role"] != "admin":
                raise AuthzError("forbidden", "只有项目组管理员可以执行观测类型转组", status=403)
            if not obs_type or to_group not in GROUPS:
                raise AuthzError("bad_target", "观测类型与目标项目组必须有效，转组未执行", status=400)
            from_group = self.type_groups.get(obs_type)
            if from_group == to_group:
                raise AuthzError("no_change", f"观测类型「{obs_type}」已属于目标项目组", status=400)
            op_time = op_time or datetime.now().isoformat(timespec="seconds")
            affected = [gid for gid in {from_group, to_group} if gid]
            self._check_order(affected, op_time)

            snap = self._snapshot()
            try:
                self.type_groups[obs_type] = to_group
                points = [
                    row
                    for row in store.rows(HYDRO_MODULE)
                    if str(row.get("观测类型") or "") == obs_type
                ]
                for point in points:
                    old_group = str(point.get("所属项目组") or from_group or "")
                    self._stamp_owner(point, to_group, "观测类型转组")
                    # 历史观测记录保留原组审计基准，只追加转组痕迹
                    point.setdefault("history", []).append(
                        {
                            "op": "观测类型转组",
                            "operator": session.username,
                            "at": op_time,
                            "audit_group": old_group,  # 转组前基准原组
                            "audit_group_name": self.group_name(old_group),
                            "group_to": to_group,
                            "group_to_name": self.group_name(to_group),
                        }
                    )
                    self._append_audit(
                        op="观测类型转组",
                        point=point,
                        group_from=old_group,
                        group_to=to_group,
                        operator=session.username,
                        op_time=op_time,
                    )
                for gid in affected:
                    self.group_ops[gid] = op_time
                # 权限更新与缓存失效原子落地：版本推进后，所有旧会话立即不能再提交
                self.auth_version += 1
                self._invalidate_other_sessions(session)
            except Exception:
                self._restore(snap)
                raise
            return {
                "obs_type": obs_type,
                "from_group": from_group,
                "to_group": to_group,
                "affected_points": len(points),
                "auth_version": self.auth_version,
            }

    def revoke(
        self,
        token: str,
        username: str,
        group_id: str,
        op_time: str | None = None,
    ) -> dict[str, Any]:
        """撤权：收回账号在某项目组的作业权，并立即使其缓存会话失效。"""
        with self._lock:
            session = self._require_fresh_session(token)
            admin = self.user_view(session.username)
            if admin["role"] != "admin":
                raise AuthzError("forbidden", "只有项目组管理员可以撤权", status=403)
            if username not in USERS:
                raise AuthzError("unknown_user", f"账号 {username} 不存在", status=404)
            if group_id not in GROUPS:
                raise AuthzError("bad_target", f"项目组 {group_id} 不存在", status=400)
            op_time = op_time or datetime.now().isoformat(timespec="seconds")
            self._check_order([group_id], op_time)

            snap = self._snapshot()
            try:
                target = USERS[username]
                GROUPS[group_id]["membership"][username] = False
                self.group_ops[group_id] = op_time
                self.auth_version += 1
                self._append_audit(
                    op="撤权",
                    point=None,
                    group_from=group_id,
                    group_to=None,
                    operator=session.username,
                    op_time=op_time,
                    detail=f"收回 {target['name']}（{username}）在 {self.group_name(group_id)} 的作业权",
                )
                # 被撤账号的全部会话立即失效；其他会话版本落后也不能再提交
                for other in self.sessions.values():
                    if other.username == username:
                        other.alive = False
                self._invalidate_other_sessions(session)
            except Exception:
                self._restore(snap)
                raise
            return {"username": username, "group": group_id, "auth_version": self.auth_version}

    def grant(self, token: str, username: str, group_id: str) -> dict[str, Any]:
        """授权：把账号编入项目组（演示/恢复用），同样推进版本使缓存失效。"""
        with self._lock:
            session = self._require_fresh_session(token)
            if USERS[session.username]["role"] != "admin":
                raise AuthzError("forbidden", "只有项目组管理员可以授权", status=403)
            if username not in USERS or group_id not in GROUPS:
                raise AuthzError("bad_target", "账号或项目组无效", status=400)
            GROUPS[group_id]["membership"][username] = True
            self.auth_version += 1
            self._invalidate_other_sessions(session)
            self._append_audit(
                op="授权",
                point=None,
                group_from=None,
                group_to=group_id,
                operator=session.username,
                detail=f"{username} 编入 {self.group_name(group_id)}",
            )
            return {"username": username, "group": group_id, "auth_version": self.auth_version}

    def _invalidate_other_sessions(self, current: Session) -> None:
        """权限变更后：操作者会话对齐新版本；其他会话版本落后——读可继续，写必拒绝。"""
        for other in self.sessions.values():
            if not other.alive:
                continue
            other.auth_version = self.auth_version if other.token == current.token else self.auth_version - 1

    def _append_audit(
        self,
        *,
        op: str,
        point: dict[str, Any] | None,
        operator: str,
        group_from: str | None = None,
        group_to: str | None = None,
        op_time: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.audit.append(
            {
                "op": op,
                "point_id": point.get("id") if point else None,
                "观测编号": point.get("观测编号") if point else None,
                "group_from": group_from,
                "group_from_name": self.group_name(group_from) if group_from else None,
                "group_to": group_to,
                "group_to_name": self.group_name(group_to) if group_to else None,
                "operator": operator,
                "at": op_time or datetime.now().isoformat(timespec="seconds"),
                "detail": detail,
            }
        )

    def list_audit(self, token: str) -> list[dict[str, Any]]:
        with self._lock:
            self._require_session(token)
            return list(self.audit)


# 初始组员：编辑员默认编入各自单位的项目组
def _seed_membership() -> None:
    GROUPS["G-A1"]["membership"] = {"editor_a1": True}
    GROUPS["G-A2"]["membership"] = {"editor_a2": True}
    GROUPS["G-B1"]["membership"] = {"editor_b1": True}


_seed_membership()

authz = AuthzDomain()

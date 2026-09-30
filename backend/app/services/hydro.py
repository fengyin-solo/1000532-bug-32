"""水文地质业务规则：状态流转、字段校验、筛选口径与写授权都收在这里。

授权口径（修复后）：
- 所有改动观测数据的入口（登记、动作、转组）都走 services.access 的统一闸门；
- 跨单位提交一律拒绝；只读账号不能改动观测数据；会话失效（转组/撤权后旧票据）拒绝；
- 归属以观测台账上的「当前项目组」为准，列表/详情/工作台/钻孔引用共用同一来源。
"""
from __future__ import annotations

from typing import Any

from app.iam import Account, iam
from app.services.access import access
from app.store import store

MODULE = "hydro"
REQUIRED_FIELDS = ["观测编号", "观测类型", "所在钻孔"]
STATUS_ORDER = ["待观测", "已观测", "数据异常", "已复核"]
ACTION_RULES = {"记录观测": "已观测", "标记异常": "数据异常", "复核数据": "已复核"}
NEGATIVE_ACTIONS = []


class HydroService:
    def list_entries(
        self,
        *,
        account: Account | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if account is not None:
            # 列表按当前账号同单位过滤；只读账号能看同单位，但不会拿到可写口径
            from app.iam import visible_group_ids

            visible = set(visible_group_ids(account))
            rows = [row for row in rows if row.get("项目组") in visible]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("观测编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any], *, account: Account) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field_name for field_name in REQUIRED_FIELDS if not str(values.get(field_name) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field_name: values.get(field_name) for field_name in REQUIRED_FIELDS})
        # 其余业务字段一并落台账
        for key in ("静止水位", "降深", "出水量", "观测日期", "观测状态"):
            if values.get(key) is not None:
                entry[key] = values.get(key)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        # 归属以观测类型当前配置的项目组为准；跨单位登记在这里被拒，行不落库
        access.assign_new_entry(entry, account)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        account: Account,
        now: int,
    ) -> tuple[dict[str, Any] | None, str]:
        # 统一授权闸门：只读拒绝 / 跨单位拒绝 / 非本组拒绝 / 已撤权拒绝
        entry = access.authorize_write(account, entry_id, now=now)
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于水文地质可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        with iam.lock:
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            access.rebuild_projections_locked()
            # 观测动作按事件时点的当前组进审计；历史记录不随转组改写
            access._append_audit_locked(  # noqa: SLF001 - 同域服务内落审计
                entry,
                action=action,
                op_time=now,
                note=f"状态置为「{target}」，审计归属当前项目组 {entry.get('项目组')}",
                actor=account,
            )
        return entry, f"水文观测点已{action}"

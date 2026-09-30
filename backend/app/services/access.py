"""水文观测点的授权、归属与失效链路。

修复点对应关系：
- 归属字段（项目组/单位）挂在观测台账行上，一切判断以「当前项目组」为准；
- 跨单位提交一律拒绝；只读账号（viewer）不能改动观测数据；
- 转组在 iam 全局锁 + epoch 内原子完成：抬 epoch（旧会话立即失效）→ 改台账
  → 重建工作台投影 → 回填钻孔共享引用 → 三处一致性校验；任何一步失败回滚业务数据，
  epoch 不回退（未成功时旧会话同样不得继续提交）；
- 变更结论同时落到三处：观测台账（行字段）、工作台清单（投影）、钻孔引用页（共享引用），
  每次都从同一份台账重建投影并做一致性断言，三处口径始终统一；
- 历史观测记录按原组审计基准保留：审计台账只增不改，转组前的记录仍记原组；
- 共享引用在转组后回填到原钻孔（同单位跨组共享）；
- 存量无归属点按采集顺序（id 升序）补数：能按观测类型映射的先映射，剩余点轮派到同钻孔属组所在单位的各组。
"""
from __future__ import annotations

from typing import Any, Callable

from app.iam import (
    GROUPS,
    ROLE_ADMIN,
    Account,
    AuthError,
    iam,
    visible_group_ids,
)
from app.store import store

HYDRO = "hydro"
BOREHOLE = "borehole"

# 台账行上的归属/结论文段
F_GROUP = "项目组"
F_UNIT = "所属单位"
F_CONCLUSION = "转组结论"
F_CONCLUSION_AT = "结论时间"
F_ORIGIN = "归属来源"  # 类型映射 / 采集顺序补数 / 转组


class TransferConflict(AuthError):
    """转组本身被业务规则拒绝（迟到、跨单位、目标组不合法）。"""


class ConsistencyError(Exception):
    """三处口径不一致：事务立即回滚，绝不让半成品结论落地。"""


class AccessService:
    def __init__(self) -> None:
        self._hydrated = False
        # 测试/故障注入：在台账已改、投影重建之间抛错，验证「未成功旧会话仍失效」
        self.transfer_fault: Callable[[], None] | None = None

    def reset(self) -> None:
        """测试隔离：清空投影与审计，归属补数标记复位（业务路径不调用）。"""
        with iam.lock:
            self._workbench = []
            self._audit.clear()
            self.transfer_fault = None
            self._hydrated = False

    # ============================================================ 初始化补数

    def hydrate(self) -> None:
        """启动补数：钻孔属组 + 无归属水文点按采集顺序补归属，并落历史审计基准。"""
        with iam.lock:
            if self._hydrated:
                return
            for row in store.rows(BOREHOLE):
                bore_no = str(row.get("钻孔编号") or "")
                group_id = iam.group_of_borehole(bore_no)
                row.setdefault(F_GROUP, group_id or "")
                row.setdefault(F_UNIT, GROUPS[group_id]["单位"] if group_id else "")
                row.setdefault("共享水文引用", [])
            self._backfill_ownership()
            self.rebuild_projections_locked()
            self._hydrated = True

    def _backfill_ownership(self) -> None:
        """存量无归属点按采集顺序（id 升序）补数。"""
        rows = sorted(store.rows(HYDRO), key=lambda r: int(r.get("id", 0)))
        # 同单位轮派游标：类型映射不到的点，按其钻孔属组所在单位的项目组循环补
        unit_cursor: dict[str, int] = {}
        for row in rows:
            if row.get(F_GROUP) and row.get(F_UNIT):
                continue  # 已归属不动
            obs_type = str(row.get("观测类型") or "").strip()
            bore_no = str(row.get("所在钻孔") or "").strip()
            group_id = iam.group_of_type(obs_type)
            source = "观测类型映射"
            if group_id is None:
                bore_group = iam.group_of_borehole(bore_no)
                unit = GROUPS[bore_group]["单位"] if bore_group else next(iter({g["单位"] for g in GROUPS.values()}))
                same_unit_groups = [gid for gid, meta in GROUPS.items() if meta["单位"] == unit]
                idx = unit_cursor.get(unit, 0)
                group_id = same_unit_groups[idx % len(same_unit_groups)]
                unit_cursor[unit] = idx + 1
                source = "采集顺序补数"
            self._assign_locked(row, group_id, source, op_time=None, conclusion=None)
            # 存量观测的历史记录按原组（即补数确定的当时属组）进入审计基准
            self._append_audit_locked(
                row,
                action="历史观测",
                op_time=self._seed_time(row),
                note=f"历史观测记录按原组审计基准保留（{source}）",
            )

    @staticmethod
    def _seed_time(row: dict[str, Any]) -> int:
        raw = str(row.get("观测日期") or "2026-09-01")
        digits = "".join(ch for ch in raw if ch.isdigit())
        try:
            return int(digits[:8])
        except ValueError:
            return 20260901

    # ============================================================ 台账写

    def _assign_locked(
        self,
        row: dict[str, Any],
        group_id: str,
        source: str,
        *,
        op_time: int | None,
        conclusion: str | None,
    ) -> None:
        meta = iam.group(group_id)
        row[F_GROUP] = group_id
        row[F_UNIT] = meta["单位"]
        row[F_ORIGIN] = source
        if conclusion is not None:
            row[F_CONCLUSION] = conclusion
        if op_time is not None:
            row[F_CONCLUSION_AT] = op_time

    def assign_new_entry(self, row: dict[str, Any], account: Account) -> dict[str, Any]:
        """新登记观测点：归属以观测类型当前配置的项目组为准，登记即台账，进审计。"""
        with iam.lock:
            obs_type = str(row.get("观测类型") or "").strip()
            bore_no = str(row.get("所在钻孔") or "").strip()
            group_id = iam.group_of_type(obs_type) or iam.group_of_borehole(bore_no)
            if group_id is None:
                raise AuthError("bad_ownership", "该观测类型与钻孔都无法确定归属项目组，登记被拒绝")
            if account.角色 != ROLE_ADMIN and not iam.can_manage_group(account, group_id):
                target = iam.group(group_id)
                raise AuthError(
                    "cross_unit",
                    f"归属项目组「{target['名称']}」不属于 {account.名称} 所在单位，跨单位提交一律拒绝",
                )
            self._assign_locked(row, group_id, "观测类型映射", op_time=None, conclusion=None)
            self.rebuild_projections_locked()
            self._append_audit_locked(row, action="登记观测点", op_time=None, note=f"归属：{group_id}")
            return row

    # ============================================================ 写授权

    def authorize_write(self, account: Account, entry_id: int, *, now: int) -> dict[str, Any]:
        """观测数据写操作的统一闸门，返回台账行，失败抛 AuthError。"""
        with iam.lock:
            if account.角色 not in ("editor", ROLE_ADMIN):
                raise AuthError("readonly", "只读账号不能改动观测数据")
            row = store.find(HYDRO, entry_id)
            if row is None:
                raise AuthError("not_found", f"水文观测点 {entry_id} 不存在或已归档")
            if account.角色 != ROLE_ADMIN and account.单位 != row.get(F_UNIT):
                raise AuthError(
                    "cross_unit",
                    f"{row.get('观测编号')} 现归属{row.get(F_UNIT)}，跨单位提交一律拒绝",
                )
            if account.角色 != ROLE_ADMIN and row.get(F_GROUP) not in account.项目组:
                raise AuthError(
                    "not_my_group",
                    f"观测点已转至「{iam.group(row[F_GROUP])['名称']}」，请重新进入工作台获取最新归属",
                )
            if iam.is_revoked_at(account.id, now):
                raise AuthError("revoked", "账号已撤权，操作被拒绝")
            return row

    # ============================================================ 转组（原子）

    def transfer(
        self,
        entry_id: int,
        target_group: str,
        *,
        actor: Account,
        op_time: int,
        reason: str,
    ) -> dict[str, Any]:
        """原子转组。并发撤权/转组按操作时间定序：只有先到的生效。"""
        with iam.lock:
            row = store.find(HYDRO, entry_id)
            if row is None:
                raise TransferConflict("not_found", f"水文观测点 {entry_id} 不存在或已归档")
            target = iam.group(target_group)
            if actor.角色 != ROLE_ADMIN:
                # 经办人只能在同单位内调整，且不能跨单位
                if actor.单位 != row.get(F_UNIT) or target["单位"] != row.get(F_UNIT):
                    raise TransferConflict(
                        "cross_unit",
                        f"观测点属{row.get(F_UNIT)}，目标「{target['名称']}」属{target['单位']}，跨单位提交一律拒绝",
                    )
                if row.get(F_GROUP) not in actor.项目组:
                    raise TransferConflict("forbidden", "只能转出本组名下的观测点")
            else:
                if target["单位"] != row.get(F_UNIT):
                    raise TransferConflict(
                        "cross_unit",
                        f"平台管理员也不能跨单位转组：{row.get(F_UNIT)} → {target['单位']}",
                    )
            if iam.is_revoked_at(actor.id, op_time):
                raise TransferConflict("revoked", "操作账号在该操作时间已撤权，提交被拒绝")
            if row.get(F_GROUP) == target_group:
                raise TransferConflict("no_change", f"观测点已在「{target['名称']}」，无需转组")
            # 操作时间定序：已有不早于本次的结论（迟到的转组冲突）→ 只有先到的生效
            prior = row.get(F_CONCLUSION_AT)
            if prior is not None and int(prior) >= op_time:
                raise TransferConflict(
                    "conflict",
                    f"转组冲突：该点在 t={prior} 已有生效结论，迟到操作（t={op_time}）被拒绝",
                )

            # —— 原子区段：先备份，再抬 epoch（旧会话从这一刻起全部失效）——
            snapshot = {
                "row": dict(row),
                "audit_len": len(self._audit),
                "workbench": _copy_projection(self._workbench),
                "bore_refs": {b["钻孔编号"]: list(b.get("共享水文引用", [])) for b in store.rows(BOREHOLE)},
            }
            iam.bump_epoch_locked()
            try:
                old_group = row.get(F_GROUP)
                conclusion = f"{GROUPS[old_group]['名称']} → {target['名称']}（{reason}）"
                self._assign_locked(
                    row,
                    target_group,
                    "转组",
                    op_time=op_time,
                    conclusion=conclusion,
                )
                if self.transfer_fault is not None:
                    fault = self.transfer_fault
                    self.transfer_fault = None
                    fault()
                self.rebuild_projections_locked()
                self._append_audit_locked(
                    row,
                    action="观测类型转组",
                    op_time=op_time,
                    note=f"{old_group} → {target_group}；{reason}",
                    actor=actor,
                )
                self.assert_consistent_locked()
            except Exception:
                # 回滚业务数据，但 epoch 不回退：未成功时旧会话同样不得继续提交
                row.clear()
                row.update(snapshot["row"])
                del self._audit[snapshot["audit_len"]:]
                self._workbench = snapshot["workbench"]
                for b in store.rows(BOREHOLE):
                    refs = snapshot["bore_refs"].get(b["钻孔编号"])
                    if refs is not None:
                        b["共享水文引用"] = refs
                raise
            return row

    # ============================================================ 三处口径投影

    def rebuild_projections_locked(self) -> None:
        """从观测台账同一份数据重建：工作台清单投影 + 钻孔共享引用。"""
        hydro_rows = store.rows(HYDRO)
        # 1) 工作台清单
        workbench: list[dict[str, Any]] = []
        for row in sorted(hydro_rows, key=lambda r: int(r.get("id", 0))):
            group_id = row.get(F_GROUP) or ""
            workbench.append({
                "id": row.get("id"),
                "观测编号": row.get("观测编号"),
                "观测类型": row.get("观测类型"),
                "所在钻孔": row.get("所在钻孔"),
                "status": row.get("status"),
                "pending": row.get("pending"),
                F_GROUP: group_id,
                F_UNIT: row.get(F_UNIT),
                "项目组名称": GROUPS[group_id]["名称"] if group_id in GROUPS else group_id,
                F_CONCLUSION: row.get(F_CONCLUSION, ""),
                F_CONCLUSION_AT: row.get(F_CONCLUSION_AT, ""),
            })
        self._workbench = workbench

        # 2) 钻孔引用页：钻孔看到同单位内全部水文点；跨组的标为共享引用并回填结论
        for bore in store.rows(BOREHOLE):
            bore_no = bore.get("钻孔编号")
            refs: list[dict[str, Any]] = []
            for row in hydro_rows:
                if row.get("所在钻孔") != bore_no:
                    continue
                group_id = row.get(F_GROUP) or ""
                is_shared = group_id != bore.get(F_GROUP)
                refs.append({
                    "观测编号": row.get("观测编号"),
                    "观测类型": row.get("观测类型"),
                    F_GROUP: group_id,
                    "项目组名称": GROUPS[group_id]["名称"] if group_id in GROUPS else group_id,
                    "跨组共享": is_shared,
                    F_CONCLUSION: row.get(F_CONCLUSION, ""),
                    F_CONCLUSION_AT: row.get(F_CONCLUSION_AT, ""),
                })
            bore["共享水文引用"] = refs
        self._backfill_shared_refs_locked()

    def _backfill_shared_refs_locked(self) -> None:
        """共享引用需回填：每条跨组引用补齐原组/新组名称，便于钻孔页审计口径统一。"""
        for bore in store.rows(BOREHOLE):
            for ref in bore.get("共享水文引用", []):
                ref["引用来源"] = "观测台账" if not ref.get("跨组共享") else "转组共享回填"
                ref.setdefault("原组备注", "")
                if ref.get("跨组共享") and ref.get(F_CONCLUSION):
                    ref["原组备注"] = f"转组前归钻孔属组，现共享：{ref[F_CONCLUSION]}"

    def assert_consistent_locked(self) -> None:
        """断言三处（台账 / 工作台清单 / 钻孔引用页）口径统一，不一致即回滚。"""
        rows = store.rows(HYDRO)
        by_id = {int(r["id"]): r for r in rows}
        if len(self._workbench) != len(rows):
            raise ConsistencyError("工作台清单条数与观测台账不一致")
        for item in self._workbench:
            row = by_id.get(int(item["id"]))
            if row is None:
                raise ConsistencyError("工作台清单出现台账外的观测点")
            for field_name in (F_GROUP, F_UNIT, F_CONCLUSION, F_CONCLUSION_AT):
                if (row.get(field_name, "") or "") != (item.get(field_name, "") or ""):
                    raise ConsistencyError(f"工作台清单字段 {field_name} 与观测台账不一致")
        for bore in store.rows(BOREHOLE):
            for ref in bore.get("共享水文引用", []):
                row = next((r for r in rows if r.get("观测编号") == ref["观测编号"]), None)
                if row is None:
                    raise ConsistencyError("钻孔引用页出现台账外的观测点")
                if (row.get(F_GROUP) or "") != (ref.get(F_GROUP) or ""):
                    raise ConsistencyError("钻孔引用页归属与观测台账不一致")
                if (row.get(F_CONCLUSION, "") or "") != (ref.get(F_CONCLUSION, "") or ""):
                    raise ConsistencyError("钻孔引用页转组结论与观测台账不一致")

    # ============================================================ 审计

    _audit: list[dict[str, Any]] = []

    def _append_audit_locked(
        self,
        row: dict[str, Any],
        *,
        action: str,
        op_time: int | None,
        note: str,
        actor: Account | None = None,
    ) -> None:
        self._audit.append({
            "观测编号": row.get("观测编号"),
            "观测点id": row.get("id"),
            "动作": action,
            "操作时间": op_time,
            "审计归属组": row.get(F_GROUP),  # 事件发生时的组：历史记录按原组保留
            "审计归属单位": row.get(F_UNIT),
            "操作人": actor.id if actor else "存量补数",
            "说明": note,
        })

    def audit_trail(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        with iam.lock:
            if entry_id is None:
                return [dict(item) for item in self._audit]
            return [dict(item) for item in self._audit if item["观测点id"] == entry_id]

    # ============================================================ 读口径

    def workbench_for(self, account: Account) -> dict[str, Any]:
        """工作台清单：按当前账号可见的项目组过滤，归属字段全部取自实时投影。"""
        with iam.lock:
            visible = set(visible_group_ids(account))
            items = [dict(item) for item in self._workbench if item[F_GROUP] in visible]
            for item in items:
                gid = item[F_GROUP]
                item["可写"] = (
                    account.角色 in ("editor", ROLE_ADMIN)
                    and (account.角色 == ROLE_ADMIN or iam.can_manage_group(account, gid))
                )
            return {"items": items, "total": len(items), "auth_epoch": iam.auth_epoch}

    def detail_for(self, account: Account, entry_id: int) -> dict[str, Any]:
        """观测详情：带实时归属与当前账号对该点的可写判定（工作台/详情/钻孔三处同源）。"""
        with iam.lock:
            row = store.find(HYDRO, entry_id)
            if row is None:
                raise AuthError("not_found", f"水文观测点 {entry_id} 不存在或已归档")
            gid = row.get(F_GROUP) or ""
            writable = (
                account.角色 in ("editor", ROLE_ADMIN)
                and (
                    account.角色 == ROLE_ADMIN
                    or (account.单位 == row.get(F_UNIT) and gid in account.项目组)
                )
            )
            detail = dict(row)
            detail["项目组名称"] = GROUPS[gid]["名称"] if gid in GROUPS else gid
            detail["当前账号可写"] = writable
            detail["审计记录"] = self.audit_trail(entry_id)
            return detail


def _copy_projection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in items]


access = AccessService()

"""授权与失效链路测试：逐条覆盖「观测类型转组」后的修复要求。

覆盖点：
1. 归属以当前项目组为准（转组后工作台/详情/钻孔引用三处同步）
2. 跨单位提交一律拒绝
3. 只读账号不能改动观测数据
4. 缓存失效与权限更新原子落地；未成功时旧会话仍可按原权限提交
5. 重新进入工作台重新取一次归属，旧会话转新鲜
6. 变更结论三处（台账/工作台/钻孔引用）口径统一
7. 历史观测记录按原组审计基准保留
8. 共享引用需回填
9. 存量无归属点按采集顺序补数
10. 并发撤权与转组按操作时间定序，先到者生效
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def domain():
    """每个用例拿到重置后的授权域（含重新补数）。"""
    from app.authz import authz

    authz.reset()
    yield authz
    authz.reset()


def login(domain, username: str):
    return domain.login(username)


# ---- 9. 存量无归属点按采集顺序补数 ---------------------------------------------

def test_backfill_assigns_by_collection_order_and_keeps_source(domain):
    points = {p["观测编号"]: p for p in store_rows(domain)}
    # 有类型映射的按映射回填
    assert points["HYDR-0001"]["所属项目组"] == "G-A1"
    assert points["HYDR-0002"]["所属项目组"] == "G-A2"
    assert points["HYDR-0001"]["归属来源"] == "类型映射"
    # 无映射的存量点（水位长期观测）回补兜底组，并留下采集顺序补数痕迹
    p3 = points["HYDR-0003"]
    assert p3["所属项目组"] == "G-A1"
    assert p3["归属来源"] == "采集顺序补数"
    assert p3["所属单位"] == "地质一大队"
    audit_ops = [a for a in domain.audit if a["op"] == "归属补数"]
    assert len(audit_ops) == 3
    # 按 id 升序（采集顺序）执行
    assert [a["point_id"] for a in audit_ops if a["point_id"]] == sorted(
        a["point_id"] for a in audit_ops if a["point_id"]
    )


def test_backfill_runs_once(domain):
    before = len(domain.audit)
    domain.backfill_ownership()
    assert len(domain.audit) == before


def store_rows(domain):
    from app.store import store

    return store.rows("hydro")


# ---- 1/6. 归属以当前项目组为准；三处口径统一 ------------------------------------

def test_transfer_moves_ownership_everywhere(domain):
    admin = login(domain, "admin_a")
    result = domain.transfer_type(admin.token, "非稳定流抽水试验", "G-B1", "2026-09-10T09:00:00")
    assert result["affected_points"] == 1

    from app.store import store

    point = next(p for p in store.rows("hydro") if p["观测编号"] == "HYDR-0002")
    assert point["所属项目组"] == "G-B1"
    assert point["所属单位"] == "地质二大队"

    # 台账（一队视角看不到，二队视角看得到且归属最新）
    a1 = login(domain, "editor_a2")
    ledger = domain.ledger(a1.token)
    assert all(item["观测编号"] != "HYDR-0002" for item in ledger["items"])

    b1 = login(domain, "editor_b1")
    bench = domain.workbench(b1.token)
    moved = next(item for item in bench["items"] if item["观测编号"] == "HYDR-0002")
    assert moved["所属项目组"] == "G-B1"
    assert moved["所属项目组名称"] == "第三水文组"
    assert moved["can_edit"] is True

    # 观测详情同样取最新归属
    detail = domain.detail(b1.token, moved["id"])
    assert detail["所属项目组"] == "G-B1"

    # 钻孔引用页（8. 共享引用回填）
    refs = domain.borehole_references(b1.token, "BORE-0002")
    assert refs["total"] == 1
    assert refs["items"][0]["观测编号"] == "HYDR-0002"
    assert refs["items"][0]["所属项目组"] == "G-B1"


def test_three_surfaces_share_one_projection(domain):
    admin = login(domain, "admin_a")
    wb = domain.workbench(admin.token)
    ledger = domain.ledger(admin.token)
    for item in ledger["items"]:
        refs = domain.borehole_references(admin.token, item["所在钻孔"])
        ref = next(r for r in refs["items"] if r["id"] == item["id"])
        bench = next(w for w in wb["items"] if w["id"] == item["id"])
        for key in ("所属项目组", "所属项目组名称", "所属单位", "观测状态", "can_edit"):
            assert ref[key] == item[key] == bench[key]


# ---- 2. 跨单位提交一律拒绝 -----------------------------------------------------

def test_cross_unit_submit_rejected(domain):
    admin = login(domain, "admin_a")
    # HYDR-0002 原属 A 队 G-A2，转给 B 队
    domain.transfer_type(admin.token, "非稳定流抽水试验", "G-B1", "2026-09-10T09:00:00")

    from app.authz import AuthzError

    a2 = login(domain, "editor_a2")
    domain.refresh_session(a2)  # 即便重新进过工作台拿到最新归属，跨单位仍然拒绝
    with pytest.raises(AuthzError) as exc:
        domain.submit_action(a2.token, 2, "记录观测")
    assert exc.value.code == "not_found"  # 跨单位连存在性都不可见

    # B 队成员可正常提交
    b1 = login(domain, "editor_b1")
    view = domain.submit_action(b1.token, 2, "记录观测")
    assert view["status"] == "已观测"


def test_cross_unit_detail_hidden(domain):
    from app.authz import AuthzError

    b1 = login(domain, "editor_b1")
    with pytest.raises(AuthzError) as exc:
        domain.detail(b1.token, 1)  # HYDR-0001 属 A 队
    assert exc.value.code == "not_found"


# ---- 3. 只读账号不能改动观测数据 ------------------------------------------------

def test_readonly_cannot_submit(domain):
    from app.authz import AuthzError

    viewer = login(domain, "viewer_a")
    with pytest.raises(AuthzError) as exc:
        domain.submit_action(viewer.token, 1, "记录观测")
    assert exc.value.code == "readonly"
    # 只读账号仍然可以看本单位数据
    bench = domain.workbench(viewer.token)
    assert any(item["id"] == 1 for item in bench["items"])
    assert all(item["can_edit"] is False for item in bench["items"])


# ---- 4/5. 旧会话不得提交；回工作台刷新后恢复；原子失败不留半状态 -----------------

def test_stale_session_blocked_until_workbench_refresh(domain):
    from app.authz import AuthzError

    editor = login(domain, "editor_a1")
    assert domain.auth_version == editor.auth_version
    # 管理员转组推动授权版本
    admin = login(domain, "admin_a")
    domain.transfer_type(admin.token, "稳定流抽水试验", "G-A2", "2026-09-10T09:00:00")
    assert editor.auth_version != domain.auth_version

    # 缓存里残留的旧任务直接提交 → 拒绝
    with pytest.raises(AuthzError) as exc:
        domain.submit_action(editor.token, 1, "记录观测")
    assert exc.value.code == "permission_stale"

    # 重新进入工作台 → 重新取归属，会话恢复新鲜（此时成员关系决定能否写）
    domain.workbench(editor.token)
    # editor_a1 已不在新组 G-A2，刷新后拿到的是「归属非本组」而不是陈旧错误
    with pytest.raises(AuthzError) as exc:
        domain.submit_action(editor.token, 1, "记录观测")
    assert exc.value.code == "not_my_group"

    # G-A2 成员刷新后可以提交
    a2 = login(domain, "editor_a2")
    domain.workbench(a2.token)
    view = domain.submit_action(a2.token, 1, "记录观测")
    assert view["所属项目组"] == "G-A2"


def test_atomic_transfer_rolls_back_on_failure(domain, monkeypatch):
    admin = login(domain, "admin_a")
    from app.store import store

    before = [dict(p) for p in store.rows("hydro")]
    version_before = domain.auth_version

    def boom(*_args, **_kwargs):
        raise RuntimeError("审计存储故障，模拟落地失败")

    monkeypatch.setattr(domain, "_append_audit", boom)
    with pytest.raises(RuntimeError):
        domain.transfer_type(admin.token, "稳定流抽水试验", "G-A2", "2026-09-10T09:00:00")
    monkeypatch.undo()

    # 类型映射、归属、版本号全部回到操作前
    assert domain.type_groups["稳定流抽水试验"] == "G-A1"
    assert domain.auth_version == version_before
    after = store.rows("hydro")
    for old, new in zip(before, after):
        assert old["所属项目组"] == new["所属项目组"]
        assert old["status"] == new["status"]
    # 关键：未成功落地时，旧会话仍可按原权限继续提交（不会被错误失效）
    editor = login(domain, "editor_a1")
    view = domain.submit_action(editor.token, 1, "记录观测")
    assert view["所属项目组"] == "G-A1"


def test_atomic_revoke_rolls_back_on_failure(domain, monkeypatch):
    admin = login(domain, "admin_a")
    version_before = domain.auth_version

    def boom(*_a, **_k):
        raise RuntimeError("权限表写入失败")

    monkeypatch.setattr(domain, "_append_audit", boom)
    with pytest.raises(RuntimeError):
        domain.revoke(admin.token, "editor_a1", "G-A1", "2026-09-10T10:00:00")
    monkeypatch.undo()
    assert domain.auth_version == version_before
    from app.authz import GROUPS

    assert GROUPS["G-A1"]["membership"]["editor_a1"] is True
    editor = login(domain, "editor_a1")
    assert domain.submit_action(editor.token, 1, "记录观测")["id"] == 1


# ---- 7. 历史观测记录按原组审计基准保留 ------------------------------------------

def test_history_keeps_original_group_baseline(domain):
    admin = login(domain, "admin_a")
    a1 = login(domain, "editor_a1")
    # 转组前先做一次观测，审计基准应为 G-A1
    domain.submit_action(a1.token, 1, "记录观测")
    domain.transfer_type(admin.token, "稳定流抽水试验", "G-B1", "2026-09-10T09:00:00")
    from app.store import store

    point = next(p for p in store.rows("hydro") if p["id"] == 1)
    observe_event = next(e for e in point["history"] if e["op"] == "记录观测")
    transfer_event = next(e for e in point["history"] if e["op"] == "观测类型转组")
    # 历史观测不随转组改写基准
    assert observe_event["audit_group"] == "G-A1"
    # 转组事件同时记录原组与新组
    assert transfer_event["audit_group"] == "G-A1"
    assert transfer_event["group_to"] == "G-B1"
    # 转组后 B 队再提交，新记录基准是新组
    b1 = login(domain, "editor_b1")
    domain.submit_action(b1.token, 1, "复核数据")
    point2 = domain.detail(b1.token, 1)
    last = point2["history"][-1]
    assert last["op"] == "复核数据"
    assert last["audit_group"] == "G-B1"


# ---- 8. 共享引用回填 -----------------------------------------------------------

def test_shared_borehole_references_backfilled(domain):
    # 同一钻孔被多条观测共享时，引用页全部回填
    from app.store import store

    rows = store.rows("hydro")
    rows[1]["所在钻孔"] = "BORE-0001"  # HYDR-0002 也引用 BORE-0001
    admin = login(domain, "admin_a")
    refs = domain.borehole_references(admin.token, "BORE-0001")
    codes = {item["观测编号"] for item in refs["items"]}
    assert {"HYDR-0001", "HYDR-0002"} <= codes
    assert refs["total"] == len(refs["items"]) == 2


# ---- 10. 并发撤权与转组按操作时间定序，先到者生效 --------------------------------

def test_concurrent_transfer_and_revoke_order_by_op_time(domain):
    from app.authz import AuthzError

    admin = login(domain, "admin_a")
    # t1 先到：撤权 editor_a1 在 G-A1 的作业权
    domain.revoke(admin.token, "editor_a1", "G-A1", "2026-09-10T09:00:00")
    # t2 后到的转组（时间更晚）——正常生效
    result = domain.transfer_type(
        admin.token, "稳定流抽水试验", "G-A2", "2026-09-10T09:05:00"
    )
    assert result["to_group"] == "G-A2"

    # 被撤权账号即便换会话也无法再操作
    editor = login(domain, "editor_a1")
    with pytest.raises(AuthzError) as exc:
        domain.submit_action(editor.token, 1, "记录观测")
    assert exc.value.code in {"not_my_group", "permission_stale"}

    # 乱序重放：更早时间戳的撤权必须被拒绝，且不改变已生效结论
    with pytest.raises(AuthzError) as exc:
        domain.revoke(admin.token, "editor_a2", "G-A2", "2026-09-10T09:01:00")
    assert exc.value.code == "stale_op"
    # G-A2 上 09:05 的转组结论不被 09:01 的迟到撤权影响
    assert domain.type_groups["稳定流抽水试验"] == "G-A2"


def test_concurrent_stale_revoke_does_not_revive(domain):
    from app.authz import AuthzError

    admin = login(domain, "admin_a")
    domain.transfer_type(admin.token, "稳定流抽水试验", "G-A2", "2026-09-10T11:00:00")
    # 针对源组的迟到撤权（同时间也拒绝，防止并发生效两次）
    with pytest.raises(AuthzError):
        domain.revoke(admin.token, "editor_a1", "G-A1", "2026-09-10T11:00:00")
    with pytest.raises(AuthzError):
        domain.revoke(admin.token, "editor_a1", "G-A1", "2026-09-10T10:59:00")
    assert domain.type_groups["稳定流抽水试验"] == "G-A2"


def test_revoked_session_is_killed(domain):
    from app.authz import AuthzError

    editor = login(domain, "editor_a1")
    admin = login(domain, "admin_a")
    domain.revoke(admin.token, "editor_a1", "G-A1", "2026-09-10T08:00:00")
    with pytest.raises(AuthzError) as exc:
        domain.workbench(editor.token)
    assert exc.value.code == "session_gone"


# ---- 非功能：无令牌 / 非管理员 / 非法动作 ---------------------------------------

def test_anonymous_blocked(domain):
    from app.authz import AuthzError

    with pytest.raises(AuthzError) as exc:
        domain.workbench("")
    assert exc.value.code == "unauthenticated"


def test_non_admin_cannot_transfer(domain):
    from app.authz import AuthzError

    editor = login(domain, "editor_a1")
    with pytest.raises(AuthzError) as exc:
        domain.transfer_type(editor.token, "稳定流抽水试验", "G-A2")
    assert exc.value.code == "forbidden"


def test_logout_kills_session(domain):
    from app.authz import AuthzError

    session = login(domain, "viewer_a")
    domain.logout(session.token)
    with pytest.raises(AuthzError) as exc:
        domain.ledger(session.token)
    assert exc.value.code == "session_gone"

"""授权链路 HTTP 集成测试：从登录到提交、转组、失效的完整闭环。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.authz import authz  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_state():
    """每个用例把仓库与授权域恢复到初始态，避免用例间串状态。"""
    authz.reset()
    yield
    authz.reset()


def h(token: str) -> dict[str, str]:
    return {"X-Auth-Token": token}


def login(username: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_full_transfer_invalidation_loop():
    admin = login("admin_a")
    a1 = login("editor_a1")
    b1 = login("editor_b1")

    # 转组前 A 队成员可提交
    ok = client.post(
        "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=h(a1)
    )
    assert ok.status_code == 200
    assert ok.json()["entry"]["所属项目组"] == "G-A1"

    # 只读账号被拒
    viewer = login("viewer_a")
    denied = client.post(
        "/api/hydro/1/actions", json={"values": {"action": "标记异常"}}, headers=h(viewer)
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "readonly"

    # 管理员把「稳定流抽水试验」转给 B 队
    moved = client.post(
        "/api/auth/transfer",
        json={"obs_type": "稳定流抽水试验", "to_group": "G-B1", "op_time": "2026-09-10T09:00:00"},
        headers=h(admin),
    )
    assert moved.status_code == 200
    assert moved.json()["affected_points"] == 1

    # 非本组旧会话的残留任务直接提交 → 被缓存版本拦截
    stale = client.post(
        "/api/hydro/1/actions", json={"values": {"action": "复核数据"}}, headers=h(a1)
    )
    assert stale.status_code == 403
    assert stale.json()["detail"]["code"] == "permission_stale"

    # 旧会话连详情缓存都不该采信：读侧返回 404（跨单位不可见）
    hidden = client.get("/api/hydro/1", headers=h(a1))
    assert hidden.status_code == 404

    # 重新进入工作台：重新取归属；A 队清单中不再含该点
    wb_a = client.get("/api/auth/workbench", headers=h(a1))
    assert wb_a.status_code == 200
    assert all(item["id"] != 1 for item in wb_a.json()["items"])

    # B 队工作台立即可见、可提交
    wb_b = client.get("/api/auth/workbench", headers=h(b1))
    row = next(item for item in wb_b.json()["items"] if item["id"] == 1)
    assert row["所属项目组"] == "G-B1"
    assert row["can_edit"] is True
    submit_b = client.post(
        "/api/auth/hydro-points/1/submit",
        json={"action": "复核数据", "values": {}},
        headers=h(b1),
    )
    assert submit_b.status_code == 200

    # 三处口径一致：工作台 / 台账 / 钻孔引用
    ledger = client.get("/api/auth/hydro-ledger", headers=h(b1)).json()
    refs = client.get("/api/auth/borehole-refs/BORE-0001", headers=h(b1)).json()
    ledger_row = next(item for item in ledger["items"] if item["id"] == 1)
    ref_row = refs["items"][0]
    assert ledger_row["所属项目组"] == ref_row["所属项目组"] == "G-B1"
    assert ledger_row["观测状态"] == ref_row["观测状态"] == "已复核"

    # 审计流保留原组基准
    audit = client.get("/api/auth/audit", headers=h(admin)).json()["items"]
    observe = next(e for e in audit if e["point_id"] == 1 and e["op"] == "记录观测")
    assert observe["group_to"] == "G-A1"
    transfer = next(e for e in audit if e["op"] == "观测类型转组")
    assert transfer["group_from"] == "G-A1" and transfer["group_to"] == "G-B1"


def test_concurrent_revoke_then_late_transfer_conflict():
    admin = login("admin_a")
    first = client.post(
        "/api/auth/revoke",
        json={"username": "editor_a1", "group_id": "G-A1", "op_time": "2026-09-10T09:00:00"},
        headers=h(admin),
    )
    assert first.status_code == 200
    # 迟到的同组撤权（更旧时间戳）→ 409 定序拒绝
    late = client.post(
        "/api/auth/revoke",
        json={"username": "editor_a2", "group_id": "G-A1", "op_time": "2026-09-10T08:59:00"},
        headers=h(admin),
    )
    assert late.status_code == 409
    assert late.json()["detail"]["code"] == "stale_op"
    # 先到撤权已生效：被撤账号会话死亡
    # （editor_a1 的会话在撤权前已建立于 test 外场景时会被杀掉；此处直接验证授权状态）
    assert client.get("/api/auth/workbench", headers=h(login("editor_a1"))).status_code == 200
    # 新登录的被撤成员不在组内：对 G-A1 的点无写权限（先刷新再提交拿到 not_my_group）
    new_token = login("editor_a1")
    client.get("/api/auth/workbench", headers=h(new_token))
    resp = client.post(
        "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=h(new_token)
    )
    # 注意 HYDR-0001 此时仍属 G-A1，成员标记为 False，因此拒绝
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_my_group"


def test_anonymous_and_logout():
    assert client.get("/api/auth/workbench").status_code == 401
    token = login("viewer_b")
    assert client.post("/api/auth/logout", headers=h(token)).status_code == 200
    assert client.get("/api/auth/hydro-ledger", headers=h(token)).status_code == 401

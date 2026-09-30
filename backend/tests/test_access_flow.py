"""授权与失效链路回归测试：覆盖转组、撤权、只读拦截、跨单位拒绝、原子失效与三处口径。

直接复用 TestClient；每个用例前重置内存状态，保证互不干扰。
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.iam import ACCOUNTS, iam
from app.services.access import access
from app.store import store


class AuthFlowTestBase(unittest.TestCase):
    def setUp(self) -> None:
        iam.reset()
        access.reset()
        # 恢复种子行上被补数写入的归属字段，使每次 setUp 都重新走「存量补数」
        for row in store.rows("hydro"):
            for field_name in ("项目组", "所属单位", "归属来源", "转组结论", "结论时间"):
                row.pop(field_name, None)
        for bore in store.rows("borehole"):
            for field_name in ("项目组", "所属单位", "共享水文引用"):
                bore.pop(field_name, None)
        access.hydrate()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def login(self, account_id: str) -> dict[str, str]:
        response = self.client.post("/api/auth/bootstrap", json={"account_id": account_id})
        assert response.status_code == 200, response.text
        body = response.json()
        return {"X-Session-Token": body["token"]}

    def hydro_row(self, entry_id: int) -> dict:
        return store.find("hydro", entry_id)

    def bore_refs(self, bore_no: str) -> list[dict]:
        bore = next(b for b in store.rows("borehole") if b["钻孔编号"] == bore_no)
        return bore["共享水文引用"]

    def ref_of(self, bore_no: str, obs_no: str) -> dict:
        return next(r for r in self.bore_refs(bore_no) if r["观测编号"] == obs_no)


class OwnershipBackfillTests(AuthFlowTestBase):
    def test_legacy_points_backfilled_in_collection_order(self) -> None:
        """存量无归属点：类型映射优先，映射不到的按采集顺序补数。"""
        rows = {r["id"]: r for r in store.rows("hydro")}
        self.assertEqual(rows[1]["项目组"], "A-HYDRO")   # 稳定水位观测
        self.assertEqual(rows[2]["项目组"], "A-PUMP")    # 抽水试验
        self.assertEqual(rows[3]["项目组"], "A-DRILL")   # 钻孔简易水文
        # 无类型点（BORE-0001 属 A-DRILL/一处）：按一处三组顺序补，第一个为 A-HYDRO
        self.assertEqual(rows[4]["项目组"], "A-HYDRO")
        self.assertEqual(rows[4]["归属来源"], "采集顺序补数")

    def test_history_kept_under_original_group(self) -> None:
        """历史观测记录按原组审计基准保留，转组不改写旧审计行。"""
        before = access.audit_trail(1)
        self.assertTrue(before)
        self.assertTrue(all(item["审计归属组"] == "A-HYDRO" for item in before))

        headers = self.login("platform-admin")
        response = self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "A-DRILL", "reason": "类型调整", "op_time": 5000},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        after = access.audit_trail(1)
        old_rows = [i for i in after if i["动作"] == "历史观测"]
        transfer_rows = [i for i in after if i["动作"] == "观测类型转组"]
        self.assertTrue(old_rows)
        self.assertTrue(transfer_rows)
        self.assertTrue(all(i["审计归属组"] == "A-HYDRO" for i in old_rows))
        self.assertEqual(transfer_rows[0]["审计归属组"], "A-DRILL")


class WriteAuthorizationTests(AuthFlowTestBase):
    def test_viewer_cannot_modify_observation(self) -> None:
        """只读账号不能改动观测数据（即便观测点归本组）。"""
        headers = self.login("a-viewer")  # 属于 A-HYDRO，点 1 也在 A-HYDRO
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=headers
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("只读", response.json()["detail"])

    def test_cross_unit_submission_rejected(self) -> None:
        """跨单位提交一律拒绝：二处账号动一处的点。"""
        headers = self.login("b-editor")
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=headers
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("跨单位", response.json()["detail"])

    def test_transferred_point_not_writable_by_old_group(self) -> None:
        """转组后原组账号按最新归属被拒，新组账号可写。"""
        admin = self.login("platform-admin")
        response = self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "A-DRILL", "reason": "观测类型转组", "op_time": 6000},
            headers=admin,
        )
        self.assertEqual(response.status_code, 200, response.text)

        old = self.login("a-editor")  # 原 A-HYDRO
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=old
        )
        self.assertIn(response.status_code, (403, 409))
        new_owner = self.login("a-drill")  # 现 A-DRILL
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=new_owner
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_no_session_is_fail_closed(self) -> None:
        """没有会话票据的写请求一律拒绝。"""
        response = self.client.post("/api/hydro/1/actions", json={"values": {"action": "记录观测"}})
        self.assertEqual(response.status_code, 401)


class AtomicInvalidationTests(AuthFlowTestBase):
    def test_transfer_invalidates_old_session(self) -> None:
        """缓存失效与权限更新原子落地：转组后旧票据立即不能再提交。"""
        old_headers = self.login("a-editor")
        admin = self.login("platform-admin")
        self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "A-DRILL", "reason": "类型调整", "op_time": 7000},
            headers=admin,
        )
        # 旧会话残留任务直接提交 → 409 拒绝，必须重新进入工作台
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "复核数据"}}, headers=old_headers
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("工作台", response.json()["detail"])

    def test_revoke_invalidates_old_session(self) -> None:
        """撤权后被撤账号的旧会话立即失效。"""
        target = self.login("a-editor")
        admin = self.login("platform-admin")
        response = self.client.post(
            "/api/auth/revoke", json={"account_id": "a-editor", "reason": "离岗", "op_time": 8000},
            headers=admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=target
        )
        self.assertEqual(response.status_code, 409)
        # 撤权后重新进入工作台也拿不到会话
        response = self.client.post("/api/auth/bootstrap", json={"account_id": "a-editor"})
        self.assertEqual(response.status_code, 403)

    def test_failed_transfer_keeps_old_session_invalid(self) -> None:
        """转组事务未成功：业务数据回滚，但 epoch 不回退，旧会话不得继续提交。"""
        stale_headers = self.login("a-drill")
        admin = self.login("platform-admin")

        def boom() -> None:
            raise RuntimeError("投影重建故障")

        access.transfer_fault = boom
        with self.assertRaises(RuntimeError):
            self.client.post(
                "/api/hydro/1/transfer",
                json={"target_group": "A-DRILL", "reason": "类型调整", "op_time": 9000},
                headers=admin,
            )
        # 业务数据已回滚（仍归 A-HYDRO，无结论）
        row = self.hydro_row(1)
        self.assertEqual(row["项目组"], "A-HYDRO")
        self.assertNotIn("转组结论", row)
        # 但旧会话依旧失效，不能借回滚继续提交
        response = self.client.post(
            "/api/hydro/1/actions", json={"values": {"action": "记录观测"}}, headers=stale_headers
        )
        self.assertEqual(response.status_code, 409)


class ThreeWayConsistencyTests(AuthFlowTestBase):
    def test_conclusion_written_back_to_three_places(self) -> None:
        """转组结论回写观测台账、工作台清单、钻孔引用页，三处口径统一。"""
        headers = self.login("platform-admin")
        response = self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "A-DRILL", "reason": "稳定水位改由钻探组承接", "op_time": 10000},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)

        row = self.hydro_row(1)
        self.assertEqual(row["项目组"], "A-DRILL")
        conclusion = row["转组结论"]

        # 工作台清单
        wb = access.workbench_for(ACCOUNTS["platform-admin"])["items"]
        item = next(i for i in wb if i["id"] == 1)
        self.assertEqual(item["项目组"], "A-DRILL")
        self.assertEqual(item["转组结论"], conclusion)

        # 钻孔引用页（HYDR-0001 在 BORE-0001，钻孔属 A-DRILL，转组后变为非共享）
        ref = self.ref_of("BORE-0001", "HYDR-0001")
        self.assertEqual(ref["项目组"], "A-DRILL")
        self.assertEqual(ref["转组结论"], conclusion)
        self.assertFalse(ref["跨组共享"])

        # 接口口径一致（转组已使旧票据失效，重新进入工作台后取详情）
        headers = self.login("platform-admin")
        detail = self.client.get("/api/hydro/1", headers=headers)
        self.assertEqual(detail.json()["转组结论"], conclusion)

    def test_shared_reference_backfilled_on_transfer(self) -> None:
        """同单位跨组转组后，原钻孔页保留为共享引用并回填结论。"""
        headers = self.login("platform-admin")
        # 点 2 在 BORE-0002（A-PUMP 自有），转到 A-HYDRO → 对钻孔页变成跨组共享
        self.client.post(
            "/api/hydro/2/transfer",
            json={"target_group": "A-HYDRO", "reason": "移交水文组", "op_time": 11000},
            headers=headers,
        )
        ref = self.ref_of("BORE-0002", "HYDR-0002")
        self.assertTrue(ref["跨组共享"])
        self.assertEqual(ref["项目组"], "A-HYDRO")
        self.assertIn("共享", ref["引用来源"])
        self.assertTrue(ref["原组备注"])

    def test_cross_unit_transfer_always_rejected(self) -> None:
        """转组不能跨单位：一处的点转不到二处的组。"""
        headers = self.login("platform-admin")
        response = self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "B-HYDRO", "reason": "尝试跨单位", "op_time": 12000},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("跨单位", response.json()["detail"])
        self.assertEqual(self.hydro_row(1)["项目组"], "A-HYDRO")


class OrderingTests(AuthFlowTestBase):
    def test_concurrent_revoke_ordered_by_op_time(self) -> None:
        """并发撤权按操作时间定序：先到的生效，迟到的冲突撤权被拒绝。"""
        admin = self.login("platform-admin")
        first = self.client.post(
            "/api/auth/revoke", json={"account_id": "a-editor", "reason": "撤权A", "op_time": 100},
            headers=admin,
        )
        self.assertEqual(first.status_code, 200)
        # 重新引导管理员（其会话已随 epoch 失效），再迟到撤权
        admin = self.login("platform-admin")
        late = self.client.post(
            "/api/auth/revoke", json={"account_id": "a-editor", "reason": "撤权B迟到", "op_time": 200},
            headers=admin,
        )
        self.assertEqual(late.status_code, 409)
        self.assertIn("先到", late.json()["detail"])

    def test_late_transfer_loses_to_earlier_transfer(self) -> None:
        """迟到的转组冲突：已有不早于它的结论时拒绝。"""
        admin = self.login("platform-admin")
        self.assertEqual(
            self.client.post(
                "/api/hydro/1/transfer",
                json={"target_group": "A-DRILL", "reason": "先到", "op_time": 300},
                headers=admin,
            ).status_code,
            200,
        )
        admin = self.login("platform-admin")
        # 操作时间更早的「迟到包」：定序只看操作时间
        late = self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "A-HYDRO", "reason": "迟到重放", "op_time": 200},
            headers=admin,
        )
        self.assertEqual(late.status_code, 409)
        self.assertEqual(self.hydro_row(1)["项目组"], "A-DRILL")

    def test_out_of_order_revoke_loses_when_earlier_should_win(self) -> None:
        """乱序到达：服务端按操作时间定序；已撤权后到达的任何撤权包都拒绝。"""
        admin = self.login("platform-admin")
        # 先到的包 op_time=900 先生效
        self.assertEqual(
            self.client.post(
                "/api/auth/revoke", json={"account_id": "a-pump", "reason": "晚操作先到", "op_time": 900},
                headers=admin,
            ).status_code,
            200,
        )
        admin = self.login("platform-admin")
        # op_time=800 的包后到：账号已被先到的撤权置为撤权态，迟到拒绝
        late = self.client.post(
            "/api/auth/revoke", json={"account_id": "a-pump", "reason": "早操作迟到", "op_time": 800},
            headers=admin,
        )
        self.assertEqual(late.status_code, 409)

    def test_revoke_blocks_action_at_same_and_later_time(self) -> None:
        """撤权先到：被撤账号在撤权时点及之后的写操作全部无效。"""
        admin = self.login("platform-admin")
        self.client.post(
            "/api/auth/revoke", json={"account_id": "a-drill", "reason": "先撤权", "op_time": 400},
            headers=admin,
        )
        # 服务层直接验证时间线判定
        self.assertTrue(iam.is_revoked_at("a-drill", 400))
        self.assertTrue(iam.is_revoked_at("a-drill", 500))


class WorkbenchTests(AuthFlowTestBase):
    def test_reenter_workbench_refetches_ownership(self) -> None:
        """重新进入工作台：重新引导会话并拉到最新归属，不使用缓存旧口径。"""
        editor = self.login("a-editor")
        wb1 = self.client.get("/api/workbench", headers=editor)
        self.assertEqual(next(i for i in wb1.json()["items"] if i["id"] == 1)["项目组"], "A-HYDRO")

        admin = self.login("platform-admin")
        self.client.post(
            "/api/hydro/1/transfer",
            json={"target_group": "A-DRILL", "reason": "类型转组", "op_time": 1300},
            headers=admin,
        )
        # 旧会话清单接口票据已失效 → 重新引导（重新进入工作台）
        fresh = self.login("a-editor")
        wb2 = self.client.get("/api/workbench", headers=fresh)
        item = next(i for i in wb2.json()["items"] if i["id"] == 1)
        self.assertEqual(item["项目组"], "A-DRILL")
        self.assertFalse(item["可写"])  # 归属已不在本组

    def test_viewer_sees_readonly_workbench(self) -> None:
        headers = self.login("a-viewer")
        wb = self.client.get("/api/workbench", headers=headers).json()
        self.assertTrue(wb["items"])
        self.assertTrue(all(item["可写"] is False for item in wb["items"]))


if __name__ == "__main__":
    unittest.main()

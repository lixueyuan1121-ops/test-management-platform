"""任务已选旧用例分页回归：内存数据库、真实 HTTP 路由，无模型调用。"""
import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.ai_eval import router
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import EvalQuery, Project, User
from app.models.ai_eval import EvalTask


class EvalQueryPagingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([User(id=1, username="admin", name="Admin", password_hash="x", is_platform_admin=True),
                         Project(id=1, name="P1", code="P1"), Project(id=2, name="P2", code="P2")])
        self.db.flush()
        self.db.add_all([EvalQuery(id=i, project_id=1, title=f"用例{i}", prompt=f"测试{i}",
                                   dimension=None if i <= 18 else "thinking") for i in range(1, 619)])
        self.db.add(EvalQuery(id=1000, project_id=2, title="其他项目", prompt="不应显示"))
        self.db.add_all([EvalTask(id=10, project_id=1, name="常用对话", query_ids=json.dumps(list(range(1, 19)))),
                         EvalTask(id=11, project_id=2, name="其他任务", query_ids="[1000]")])
        self.db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.db.get(User, 1)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def page(self, **filters):
        response = self.client.get("/api/ai/eval-queries", params={"project_id": 1, **filters})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def test_cursor_reaches_all_old_selected_queries(self):
        first = self.page()
        self.assertEqual(len(first), 200)
        self.assertTrue(all(q["id"] > 18 for q in first))  # 复现旧版“已选 18 条、列表 0 条”。
        loaded = list(first)
        while len(first) == 200:
            first = self.page(before_id=min(q["id"] for q in first))
            loaded.extend(first)
        self.assertEqual([q["id"] for q in loaded], list(range(618, 0, -1)))
        self.assertEqual(len([q for q in loaded if q["id"] <= 18]), 18)

    def test_unlabeled_selected_queries_visible_unless_dimension_filter_is_set(self):
        rows = self.page(eval_task_id=10)
        self.assertEqual(len(rows), 18)
        self.assertTrue(all(q["dimension"] is None for q in rows))
        self.assertEqual(self.page(eval_task_id=10, dimension="thinking"), [])
        self.assertEqual([q["id"] for q in self.page(eval_task_id=10, limit=7, before_id=12)], list(range(11, 4, -1)))

    def test_new_inserts_do_not_shift_cursor_and_other_projects_stay_isolated(self):
        first = self.page()
        self.db.add(EvalQuery(id=619, project_id=1, title="新增", prompt="新增"))
        self.db.commit()
        second = self.page(before_id=first[-1]["id"])
        self.assertEqual([q["id"] for q in second], list(range(418, 218, -1)))
        self.assertEqual(self.client.get("/api/ai/eval-queries", params={"project_id": 1, "eval_task_id": 11}).status_code, 404)

    def test_invalid_paging_parameters_are_rejected(self):
        for filters in [{"before_id": 0}, {"limit": 0}, {"limit": 501}]:
            with self.subTest(filters=filters):
                self.assertEqual(self.client.get("/api/ai/eval-queries", params={"project_id": 1, **filters}).status_code, 422)


if __name__ == "__main__":
    unittest.main()

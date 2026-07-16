# -*- coding: utf-8 -*-

from fastapi.testclient import TestClient

from app.api import create_app


class FakeRuntime:
    def __init__(self):
        self.ready = False
        self.task = None
        self._last_get_at = {}
        self.platform = {
            "code": "ke",
            "name": "贝壳",
            "startUrl": "https://sz.ke.com/ershoufang/",
            "statusCode": "WAIT_LOGIN",
            "status": "等待登录",
            "message": "等待人工登录后确认",
            "lastReadyAt": None,
            "lastKeepaliveAt": None,
        }

    async def start(self):
        return None

    async def stop(self):
        return None

    def is_ready(self):
        return self.ready

    def snapshot(self):
        return {
            "serviceStatusCode": "READY" if self.ready else "WAIT_LOGIN",
            "serviceStatus": "已就绪" if self.ready else "等待登录",
            "message": "ok" if self.ready else "waiting",
            "currentTaskId": None,
            "queueSize": 0,
            "platforms": [self.platform],
        }

    async def confirm_platform_ready(self, code: str):
        if code != "ke":
            raise KeyError(code)
        self.ready = True
        self.platform["statusCode"] = "READY"
        self.platform["status"] = "已就绪"
        self.platform["message"] = "平台已就绪"
        return self.platform

    async def enqueue_inquiry(self, request):
        if not self.ready:
            raise RuntimeError("SERVICE_NOT_READY")
        self.task = {
            "taskId": request.request_id or "task-1",
            "statusCode": "QUEUED",
            "status": "排队中",
            "createdAt": 1.0,
            "startedAt": None,
            "finishedAt": None,
            "error": None,
            "request": {
                "communityName": request.community_name,
                "area": request.area,
                "city": request.city,
                "requestId": request.request_id or "task-1",
            },
            "result": {
                "data": {
                    "quoteAvg": 85635.0,
                    "dealAvg": 71086.5,
                    "finalPrice": 71086.5,
                }
            },
        }
        return self.task

    def get_task(self, task_id: str):
        if self.task and self.task["taskId"] == task_id:
            task = dict(self.task)
            task["statusCode"] = "COMPLETED"
            task["status"] = "已完成"
            return task
        return None

    def check_get_allowed(self, task_id: str):
        import time
        now = time.time()
        last = self._last_get_at.get(task_id)
        if last is None:
            return True, 0.0
        return False, 10.0  # 测试里固定返回"还需等 10 秒"

    def register_get(self, task_id: str):
        import time
        self._last_get_at[task_id] = time.time()


def test_health_ready_returns_503_when_not_ready():
    app = create_app(runtime=FakeRuntime(), manage_runtime=False)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["data"]["serviceStatus"] == "等待登录"
    assert response.json()["data"]["serviceStatusCode"] == "WAIT_LOGIN"


def test_create_inquiry_returns_503_when_not_ready():
    app = create_app(runtime=FakeRuntime(), manage_runtime=False)

    with TestClient(app) as client:
        response = client.post(
            "/inquiries",
            json={"communityName": "绿景虹湾", "area": 89.5},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_NOT_READY"
    assert response.json()["message"] == "RPA 服务尚未就绪"


def test_confirm_ready_then_create_and_query_inquiry():
    app = create_app(runtime=FakeRuntime(), manage_runtime=False)

    with TestClient(app) as client:
        confirm = client.post("/admin/platforms/ke/confirm-ready")
        create = client.post(
            "/inquiries",
            json={"communityName": "绿景虹湾", "area": 89.5},
        )
        task = client.get(f"/inquiries/{create.json()['data']['taskId']}")

    assert confirm.status_code == 200
    assert create.status_code == 202
    assert task.status_code == 200
    assert create.json()["data"]["status"] == "排队中"
    assert task.json()["data"] == {
        "quoteAvg": 85635.0,
        "dealAvg": 71086.5,
        "finalPrice": 71086.5,
    }


def test_get_inquiry_rate_limited_after_first_call():
    """同一 taskId 第二次 GET 应返回 429，并带 retryAfter。"""
    app = create_app(runtime=FakeRuntime(), manage_runtime=False)
    with TestClient(app) as client:
        client.post("/admin/platforms/ke/confirm-ready")  # 先置就绪
        client.post("/inquiries", json={"communityName": "x", "area": 89.5})
        first = client.get("/inquiries/task-1")
        second = client.get("/inquiries/task-1")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "TOO_MANY_REQUESTS"
    assert second.json()["data"]["retryAfter"] == 10
    assert "taskId" in second.json()["data"]


def test_get_inquiry_404_not_counted_as_rate_limit():
    """查询不存在的任务返回 404，不计入限流（连续两次都 404，不触发 429）。"""
    app = create_app(runtime=FakeRuntime(), manage_runtime=False)
    with TestClient(app) as client:
        first = client.get("/inquiries/not-exist")
        second = client.get("/inquiries/not-exist")

    assert first.status_code == 404
    assert second.status_code == 404

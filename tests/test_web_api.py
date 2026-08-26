import json
from pathlib import Path

from fastapi.testclient import TestClient

from quant_lab_agent.config import Config
from quant_lab_agent.runtime import RunStore
from quant_lab_agent.web.api import create_app
from quant_lab_agent.web.identity import IdentityStore, ROLE_RESEARCHER, ROLE_REVIEWER


def make_config(tmp_path: Path) -> Config:
    return Config(
        workspace=tmp_path,
        audit_log=tmp_path / ".quant-agent" / "audit.jsonl",
        database_path=tmp_path / ".quant-agent" / "quantlab.db",
        auth_secret="a-long-test-secret-that-is-safe-for-tests",
        bootstrap_admin_username="admin",
        bootstrap_admin_password="password123",
    )


def auth(client: TestClient, username="admin", password="password123") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_web_api_lists_owned_runs_and_exposes_events(tmp_path: Path):
    config = make_config(tmp_path)
    identities = IdentityStore(config.database_path, config.auth_secret)
    identities.bootstrap_admin(config.bootstrap_admin_username, config.bootstrap_admin_password)
    admin = identities.authenticate("admin", "password123")
    store = RunStore(tmp_path)
    run_id = store.create("研究均值回归")
    identities.attach_run(run_id, admin)
    store.record_event(run_id, "tool_call", {"tool": "register_research"})

    client = TestClient(create_app(config))
    headers = auth(client)
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/runs", headers=headers).json()[0]["run_id"] == run_id
    assert client.get(f"/api/runs/{run_id}/events", headers=headers).json()[1]["tool"] == "register_research"
    assert client.get("/api/runs").status_code == 401


def test_web_api_requires_key_before_creating_run(tmp_path: Path):
    config = make_config(tmp_path)
    client = TestClient(create_app(config))
    response = client.post("/api/runs", headers=auth(client), json={"prompt": "跑一个策略"})
    assert response.status_code == 503


def test_web_api_returns_backtest_and_compares_experiments(tmp_path: Path):
    config = make_config(tmp_path)
    identities = IdentityStore(config.database_path, config.auth_secret)
    identities.bootstrap_admin(config.bootstrap_admin_username, config.bootstrap_admin_password)
    admin = identities.authenticate("admin", "password123")
    store = RunStore(tmp_path)
    left, right = store.create("left"), store.create("right")
    for run_id, sharpe in ((left, 1.0), (right, 1.4)):
        identities.attach_run(run_id, admin)
        tool_result = {"status": "success", "metrics": {"sharpe": sharpe, "max_drawdown": 0.1, "turnover": 1.0}, "artifacts": {"equity_curve": [["2025-01-01", 1.0], ["2025-01-02", 1.01]]}}
        store.record_event(run_id, "tool_call", {"tool": "run_backtest", "result": json.dumps(tool_result)})
    client = TestClient(create_app(config)); headers = auth(client)
    assert client.get(f"/api/runs/{left}/backtest", headers=headers).json()["metrics"]["sharpe"] == 1.0
    comparison = client.get(f"/api/experiments/compare?left={left}&right={right}", headers=headers).json()
    assert comparison["delta"]["sharpe"] == 0.4


def test_roles_and_approval_flow(tmp_path: Path):
    config = make_config(tmp_path)
    client = TestClient(create_app(config)); admin_headers = auth(client)
    assert client.post("/api/users", headers=admin_headers, json={"username": "alice", "password": "longpassword", "role": ROLE_RESEARCHER}).status_code == 201
    assert client.post("/api/users", headers=admin_headers, json={"username": "bob", "password": "longpassword", "role": ROLE_REVIEWER}).status_code == 201
    alice_headers, bob_headers = auth(client, "alice", "longpassword"), auth(client, "bob", "longpassword")
    identities = IdentityStore(config.database_path, config.auth_secret)
    alice = identities.authenticate("alice", "longpassword")
    run_id = RunStore(tmp_path).create("alice research")
    identities.attach_run(run_id, alice, "private")
    approval = client.post(f"/api/runs/{run_id}/approvals", headers=alice_headers, json={"comment": "请审核"})
    assert approval.status_code == 201
    assert client.post(f"/api/approvals/{approval.json()['id']}/decision", headers=alice_headers, json={"status": "approved"}).status_code == 403
    assert client.post(f"/api/approvals/{approval.json()['id']}/decision", headers=bob_headers, json={"status": "approved", "comment": "通过"}).json()["status"] == "approved"

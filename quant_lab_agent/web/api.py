"""HTTP/SSE adapter with SQLite-backed identity, roles, and approvals."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

try:
    from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install web dependencies: pip install -e '.[web]'") from exc

from ..config import Config
from ..runtime import RunStore
from ..service import execute_run
from .identity import IdentityStore, ROLE_ADMIN, ROLE_RESEARCHER, ROLE_REVIEWER, User


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(LoginRequest):
    role: str = ROLE_RESEARCHER


class CreateRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    visibility: str = "team"


class ApprovalRequest(BaseModel):
    comment: str = Field(default="", max_length=2_000)


class ApprovalDecision(BaseModel):
    status: str
    comment: str = Field(default="", max_length=2_000)


def _latest_backtest(store: RunStore, run_id: str) -> dict | None:
    for event in reversed(store.read_events(run_id)):
        if event.get("event") != "tool_call" or event.get("tool") != "run_backtest":
            continue
        try:
            result = json.loads(event.get("result", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("status") == "success":
            return {"run_id": run_id, "metrics": result["metrics"], "artifacts": result.get("artifacts", {})}
    return None


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = RunStore(config.workspace)
    identities = IdentityStore(
        config.database_path, config.auth_secret, config.auth_ttl_minutes,
        production=config.environment == "production",
    )
    identities.bootstrap_admin(config.bootstrap_admin_username, config.bootstrap_admin_password)
    bearer = HTTPBearer(auto_error=False)
    app = FastAPI(title="QuantLab API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=list(config.cors_origins), allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )

    def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
        user = identities.verify_token(credentials.credentials) if credentials else None
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录", headers={"WWW-Authenticate": "Bearer"})
        return user

    def reviewer_or_admin(user: User = Depends(current_user)) -> User:
        if user.role not in {ROLE_REVIEWER, ROLE_ADMIN}:
            raise HTTPException(status_code=403, detail="需要 reviewer 或 admin 权限")
        return user

    def admin_only(user: User = Depends(current_user)) -> User:
        if user.role != ROLE_ADMIN:
            raise HTTPException(status_code=403, detail="需要 admin 权限")
        return user

    def require_run_access(run_id: str, user: User) -> None:
        try:
            store.load(run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not identities.can_read_run(run_id, user):
            raise HTTPException(status_code=403, detail="无权访问该研究任务")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "environment": config.environment}

    @app.post("/api/auth/login")
    def login(payload: LoginRequest) -> dict:
        user = identities.authenticate(payload.username, payload.password)
        if not user:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return {"access_token": identities.issue_token(user), "token_type": "bearer", "user": user.__dict__}

    @app.get("/api/me")
    def me(user: User = Depends(current_user)) -> dict:
        return user.__dict__

    @app.post("/api/users", status_code=201)
    def create_user(payload: CreateUserRequest, _: User = Depends(admin_only)) -> dict:
        try:
            user = identities.create_user(payload.username, payload.password, payload.role)
            return user.__dict__
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs")
    def list_runs(limit: int = 50, user: User = Depends(current_user)) -> list[dict]:
        visible = identities.list_visible_ids(user)
        return [run for run in store.list_runs() if run["run_id"] in visible][:max(1, min(limit, 200))]

    @app.post("/api/runs", status_code=202)
    def create_run(payload: CreateRunRequest, background_tasks: BackgroundTasks, user: User = Depends(current_user)) -> dict:
        if not config.api_key:
            raise HTTPException(status_code=503, detail="模型 API Key 未配置")
        try:
            run_id = store.create(payload.prompt.strip())
            identities.attach_run(run_id, user, payload.visibility)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        background_tasks.add_task(execute_run, config, run_id)
        return store.load(run_id)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, user: User = Depends(current_user)) -> dict:
        require_run_access(run_id, user)
        return store.load(run_id)

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, user: User = Depends(current_user)) -> list[dict]:
        require_run_access(run_id, user)
        return store.read_events(run_id)

    @app.get("/api/runs/{run_id}/backtest")
    def get_backtest(run_id: str, user: User = Depends(current_user)) -> dict:
        require_run_access(run_id, user)
        result = _latest_backtest(store, run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="该任务没有成功的回测工件")
        return result

    @app.get("/api/experiments/compare")
    def compare_experiments(left: str, right: str, user: User = Depends(current_user)) -> dict:
        require_run_access(left, user)
        require_run_access(right, user)
        left_result, right_result = _latest_backtest(store, left), _latest_backtest(store, right)
        if left_result is None or right_result is None:
            raise HTTPException(status_code=404, detail="两次任务都需要成功的回测工件")
        delta = {key: round(right_result["metrics"][key] - left_result["metrics"][key], 6) for key in ("sharpe", "max_drawdown", "turnover")}
        return {"left": left_result, "right": right_result, "delta": delta}

    @app.post("/api/runs/{run_id}/approvals", status_code=201)
    def request_approval(run_id: str, payload: ApprovalRequest, user: User = Depends(current_user)) -> dict:
        require_run_access(run_id, user)
        return identities.request_approval(run_id, user, payload.comment)

    @app.get("/api/approvals")
    def list_approvals(user: User = Depends(current_user)) -> list[dict]:
        return identities.list_approvals(user)

    @app.post("/api/approvals/{approval_id}/decision")
    def decide_approval(approval_id: int, payload: ApprovalDecision, user: User = Depends(reviewer_or_admin)) -> dict:
        try:
            return identities.decide_approval(approval_id, user, payload.status, payload.comment)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/stream")
    async def stream_events(run_id: str, user: User = Depends(current_user)) -> StreamingResponse:
        require_run_access(run_id, user)

        async def event_stream() -> AsyncIterator[str]:
            sent = 0
            while True:
                events = store.read_events(run_id)
                for event in events[sent:]:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                sent = len(events)
                manifest = store.load(run_id)
                if manifest["status"] in {"completed", "failed", "needs_review"}:
                    yield f"event: done\ndata: {json.dumps(manifest, ensure_ascii=False)}\n\n"
                    return
                await asyncio.sleep(0.5)
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/researches")
    def list_researches(user: User = Depends(current_user)) -> list[dict]:
        path = config.workspace / ".quant-agent" / "research.json"
        if not path.exists():
            return []
        try:
            return list(json.loads(path.read_text(encoding="utf-8")).values())
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="研究记录已损坏") from exc

    static_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        @app.get("/", include_in_schema=False)
        def root() -> FileResponse:
            return FileResponse(static_dir / "index.html")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()

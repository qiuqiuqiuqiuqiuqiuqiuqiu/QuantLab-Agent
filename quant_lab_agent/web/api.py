"""HTTP/SSE adapter with SQLite-backed identity, roles, and approvals."""
from __future__ import annotations

import asyncio
import json
import hashlib
import re
import time
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from pathlib import Path

try:
    from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install web dependencies: pip install -e '.[web]'") from exc

from ..config import Config
from ..runtime import ProjectStore, RunStore
from ..service import execute_run
from ..tools.backtest import RunBacktestTool
from .identity import IdentityStore, ROLE_ADMIN, ROLE_RESEARCHER, ROLE_REVIEWER, User


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(LoginRequest):
    role: str = ROLE_RESEARCHER


class CreateRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    visibility: str = "team"


class TemplateRunRequest(BaseModel):
    template_id: str = Field(pattern="^(stock_check|compare|strategy)$")
    tickers: list[str] = Field(min_length=1, max_length=6)
    visibility: str = "team"
    project_id: str | None = Field(default=None, pattern="^project_[a-z0-9]+$")


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    thesis: str = Field(min_length=10, max_length=2_000)
    universe: list[str] = Field(default_factory=list, max_length=30)
    benchmark: str = Field(default="SPY", min_length=1, max_length=12)


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
    projects = ProjectStore(config.workspace)
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

    @app.post("/api/data-assets", status_code=201)
    async def upload_data_assets(
        files: list[UploadFile] = File(...),
        run_id: str | None = Form(None),
        user: User = Depends(current_user),
    ) -> dict:
        """Persist approved research inputs inside the local workspace with lineage metadata."""
        if run_id:
            require_run_access(run_id, user)
        allowed = {".csv", ".json", ".parquet", ".xlsx"}
        root = config.workspace / ".quant-agent" / "uploads"
        root.mkdir(parents=True, exist_ok=True)
        assets: list[dict] = []
        for upload in files:
            filename = Path(upload.filename or "").name
            if not filename or Path(filename).suffix.lower() not in allowed:
                raise HTTPException(status_code=400, detail="只支持 CSV、JSON、Parquet 或 XLSX 数据文件")
            safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem)
            asset_id = hashlib.sha256(f"{datetime.now(timezone.utc).isoformat()}:{filename}".encode()).hexdigest()[:12]
            target = root / f"{asset_id}_{safe_stem}{Path(filename).suffix.lower()}"
            digest, size = hashlib.sha256(), 0
            with target.open("wb") as handle:
                while chunk := await upload.read(64 * 1024):
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        handle.close()
                        target.unlink(missing_ok=True)
                        raise HTTPException(status_code=413, detail="单个数据文件不能超过 50 MB")
                    digest.update(chunk)
                    handle.write(chunk)
            await upload.close()
            assets.append({
                "asset_id": asset_id,
                "filename": filename,
                "relative_path": str(target.relative_to(config.workspace)),
                "bytes": size,
                "sha256": digest.hexdigest(),
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "uploaded_by": user.username,
            })
        lineage_path = config.workspace / ".quant-agent" / "data_assets.jsonl"
        with lineage_path.open("a", encoding="utf-8") as handle:
            for asset in assets:
                handle.write(json.dumps({**asset, "run_id": run_id}, ensure_ascii=False) + "\n")
        if run_id:
            store.record_event(run_id, "data_assets_uploaded", {"assets": assets})
        return {"assets": assets}

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

    @app.get("/api/projects")
    def list_projects(user: User = Depends(current_user)) -> list[dict]:
        visible_runs = {run["run_id"] for run in store.list_runs() if identities.can_read_run(run["run_id"], user)}
        output = []
        for project in projects.list():
            # A local project is visible to its owner; team-visible experiments also surface it.
            if project.get("owner") != user.username and not any(run.get("project_id") == project["project_id"] for run in store.list_runs() if run["run_id"] in visible_runs):
                continue
            experiment_runs = [run for run in store.list_runs() if run.get("project_id") == project["project_id"] and run["run_id"] in visible_runs]
            output.append({**project, "experiment_count": len(experiment_runs), "last_status": experiment_runs[0]["status"] if experiment_runs else "draft"})
        return output

    @app.post("/api/projects", status_code=201)
    def create_project(payload: CreateProjectRequest, user: User = Depends(current_user)) -> dict:
        universe = []
        for item in payload.universe:
            ticker = item.strip().upper()
            if not re.fullmatch(r"[A-Z.]{1,8}", ticker):
                raise HTTPException(status_code=400, detail="Universe tickers must be valid symbols")
            if ticker not in universe:
                universe.append(ticker)
        return projects.create(payload.name.strip(), payload.thesis.strip(), universe, payload.benchmark.strip().upper(), user.username)

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

    @app.post("/api/templates/run", status_code=201)
    def run_guided_template(payload: TemplateRunRequest, user: User = Depends(current_user)) -> dict:
        """Run a no-code, reproducible local template using explicitly labelled demo data."""
        tickers = []
        for value in payload.tickers:
            ticker = value.strip().upper()
            if not re.fullmatch(r"[A-Z.]{1,8}", ticker):
                raise HTTPException(status_code=400, detail="Tickers must contain 1–8 letters (for example NVDA or BRK.B)")
            if ticker not in tickers:
                tickers.append(ticker)
        if payload.template_id == "stock_check":
            tickers = tickers[:1]
            method = "Three-year return, volatility and drawdown profile; benchmark comparison is a later real-data step."
            prompt = f"Guided stock check: {tickers[0]} over three years"
        elif payload.template_id == "compare":
            if len(tickers) < 2:
                raise HTTPException(status_code=400, detail="The comparison template needs at least two tickers")
            method = "Compare three-year synthetic weekly return, volatility and drawdown profiles."
            prompt = "Guided stock comparison: " + " vs ".join(tickers)
        else:
            tickers = tickers[:1]
            method = "Long/cash 10/30-week moving-average trend overlay with 5 bps switching cost."
            prompt = f"Guided strategy test: {tickers[0]} 10/30-week trend overlay"
        if payload.project_id:
            try:
                project = projects.get(payload.project_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if project.get("owner") != user.username:
                raise HTTPException(status_code=403, detail="Only the project owner can add an experiment")
        run_id = store.create(prompt)
        identities.attach_run(run_id, user, payload.visibility)
        if payload.project_id:
            store.annotate(run_id, project_id=payload.project_id, experiment_type=payload.template_id)
            projects.touch(payload.project_id)
        store.record_event(run_id, "research_protocol", {"stages": [
            {"name": "Hypothesis", "status": "complete"}, {"name": "Data validation", "status": "complete", "note": "Synthetic demo dataset labelled"},
            {"name": "Baseline / experiment", "status": "running"}, {"name": "Robustness", "status": "pending"}, {"name": "Governance", "status": "pending"},
        ]})
        jobs_dir = config.workspace / ".quant-agent" / "template_jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_path = jobs_dir / f"{run_id}.json"
        result_path = config.workspace / "artifacts" / f"{run_id}_template.json"
        job = {"run_id": run_id, "template_id": payload.template_id, "tickers": tickers, "weeks": 156, "method": method}
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        store.record_event(run_id, "data_assets_registered", {"assets": [{
            "source": "QuantLab deterministic synthetic demo generator", "frequency": "weekly", "observations": 156,
            "tickers": tickers, "warning": "Demonstration data only; replace with vendor or uploaded data for a real conclusion.",
        }]})
        started = time.perf_counter()
        raw_result = RunBacktestTool(config.workspace).execute(
            "backtests/template_research.py", str(result_path.relative_to(config.workspace)), 120,
            environment={"QUANTLAB_JOB_FILE": str(job_path), "QUANTLAB_RESULT_FILE": str(result_path)},
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        result = json.loads(raw_result)
        store.record_event(run_id, "tool_call", {"round": 0, "tool": "run_backtest", "arguments": {
            "script_path": "backtests/template_research.py", "result_path": str(result_path.relative_to(config.workspace)),
        }, "result": raw_result, "latency_ms": latency_ms})
        if result.get("status") != "success":
            store.finish(run_id, "The guided template did not produce a validated artifact. See the structured failure in the event trace.", "failed")
            raise HTTPException(status_code=422, detail=result.get("message", "Template execution failed"))
        report = json.loads(result_path.read_text(encoding="utf-8")).get("report", {})
        store.record_event(run_id, "template_report", {"result": json.dumps(report, ensure_ascii=False)})
        store.record_event(run_id, "research_protocol_completed", {"stages": [
            {"name": "Hypothesis", "status": "complete"}, {"name": "Data validation", "status": "complete"},
            {"name": "Baseline / experiment", "status": "complete"}, {"name": "Robustness", "status": "needs_real_data"},
            {"name": "Governance", "status": "review_required"},
        ]})
        store.finish(run_id, f"Guided {payload.template_id} template completed for {', '.join(tickers)} using clearly labelled synthetic demonstration data. Add real vendor or uploaded data before using the result for any market decision.")
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

    @app.post("/api/runs/{run_id}/starter-index-report")
    def run_starter_index_report(run_id: str, user: User = Depends(current_user)) -> dict:
        """Run the deliberately small, no-code starter analysis for the uploaded COMP CSV.

        This is an onboarding bridge, not a general-purpose code execution endpoint:
        the script and its output path are fixed and remain inside the workspace.
        """
        require_run_access(run_id, user)
        lineage_path = config.workspace / ".quant-agent" / "data_assets.jsonl"
        has_comp_input = False
        if lineage_path.exists():
            for line in lineage_path.read_text(encoding="utf-8").splitlines():
                try:
                    asset = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if asset.get("run_id") == run_id and str(asset.get("filename", "")).lower().endswith("index_history_comp.csv"):
                    has_comp_input = True
                    break
        if not has_comp_input:
            raise HTTPException(
                status_code=400,
                detail="Upload the provided index_history_COMP.csv file to this research run before starting the guided report.",
            )
        started = time.perf_counter()
        raw_result = RunBacktestTool(config.workspace).execute(
            "backtests/comp_index_trend.py", "artifacts/comp_index_trend.json", timeout_seconds=120,
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError as exc:  # defensive: tool contract should always return JSON
            raise HTTPException(status_code=500, detail="Starter analysis returned an invalid tool response") from exc
        store.record_event(run_id, "tool_call", {
            "round": 0, "tool": "run_backtest", "arguments": {
                "script_path": "backtests/comp_index_trend.py",
                "result_path": "artifacts/comp_index_trend.json",
                "timeout_seconds": 120,
            }, "result": raw_result, "latency_ms": latency_ms,
        })
        if result.get("status") != "success":
            store.finish(run_id, "Guided Nasdaq Composite analysis could not complete. Inspect the recorded data/schema diagnosis and correct the uploaded CSV.", "needs_data")
            raise HTTPException(status_code=422, detail=result.get("message", "Starter analysis failed"))
        metrics = result["metrics"]
        answer = (
            "Guided Nasdaq Composite report completed from the uploaded weekly CSV. "
            "It evaluates the latest available three-year window with a long/cash 10/30-week moving-average trend rule and 5 bps switching cost. "
            f"Annualized return: {metrics.get('annualized_return', 0) * 100:.1f}%; "
            f"annualized volatility: {metrics.get('annualized_volatility', 0) * 100:.1f}%; "
            f"maximum drawdown: {metrics['max_drawdown'] * 100:.1f}%. "
            "This is an educational research artifact, not investment advice or an order signal."
        )
        store.record_event(run_id, "starter_report_completed", {"summary": answer})
        store.finish(run_id, answer, "completed")
        return _latest_backtest(store, run_id) or {"run_id": run_id, "metrics": metrics, "artifacts": result.get("artifacts", {})}

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
                if manifest["status"] in {"completed", "failed", "needs_review", "needs_input", "needs_data"}:
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


# The module-level ASGI app is what uvicorn imports.  Load the project's local
# .env here for the same development ergonomics as the CLI; explicit process
# environment variables still win.
app = create_app(Config.from_env(load_dotenv=True))

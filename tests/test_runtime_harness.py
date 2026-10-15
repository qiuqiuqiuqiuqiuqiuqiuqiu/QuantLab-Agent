import json
from pathlib import Path

import pytest

from quant_lab_agent import AgentResult, AgentTask, LLMResponse, PlanTask, ResearchPlan, RunDecision, ScriptedLLM
from quant_lab_agent.agent import Agent
from quant_lab_agent.runtime import AgentRegistry, AgentSpec, ArtifactStore, MultiAgentRuntime, PlanFirstCoordinator, PlanValidator, RunStore, TaskStore
from quant_lab_agent.tools.artifacts import ReadArtifactTool


def test_artifacts_are_content_addressed_immutable_and_verified(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    first = store.put_bytes(b'{"value":1}', artifact_type="backtest_result", suffix=".json", created_by="test")
    second = store.put_bytes(b'{"value":1}', artifact_type="backtest_result", suffix=".json", created_by="test")

    assert first.artifact_id == second.artifact_id
    assert store.read_bytes(first.artifact_id) == b'{"value":1}'
    assert store.get(first.artifact_id).sha256 == first.sha256
    read_result = json.loads(ReadArtifactTool(store).execute(first.artifact_id))
    assert read_result["artifact"]["artifact_id"] == first.artifact_id
    assert '"value":1' in read_result["preview"]


def test_structured_decision_is_not_inferred_from_arbitrary_prose():
    assert RunDecision.from_model_output("everything needs_review-ish") is None
    decision = RunDecision.from_model_output(json.dumps({
        "status": "needs_review", "user_message": "需要人工复核",
        "reason_codes": ["drawdown"], "evidence_artifact_ids": ["art_x"],
    }))
    assert decision and decision.status == "needs_review"
    assert decision.evidence_artifact_ids == ("art_x",)


def test_agent_fails_closed_when_structured_output_is_required(tmp_path: Path):
    agent = Agent(
        ScriptedLLM([LLMResponse(content="looks good")]), [], tmp_path,
        tmp_path / "audit.jsonl", require_structured_final=True,
    )
    assert agent.chat("check") == "looks good"
    assert agent.last_decision and agent.last_decision.status == "needs_review"
    assert agent.last_decision.reason_codes == ("invalid_structured_output",)


def test_runtime_invokes_fresh_specialist_and_persists_task(tmp_path: Path):
    created = []

    def factory():
        created.append(object())
        return Agent(
            ScriptedLLM([LLMResponse(content=json.dumps({
                "status": "completed", "user_message": "数据已校验",
                "reason_codes": [], "evidence_artifact_ids": [],
            }))]), [], tmp_path, tmp_path / "audit.jsonl", name="data_agent", require_structured_final=True,
        )

    registry = AgentRegistry()
    registry.register(AgentSpec("data_agent", "data", factory))
    runtime = MultiAgentRuntime(registry, TaskStore(tmp_path / "runtime.db"), RunStore(tmp_path))

    first = runtime.invoke("data_agent", AgentTask("data", {"ticker": "NVDA"}))
    second = runtime.invoke("data_agent", AgentTask("data", {"ticker": "MSFT"}))

    assert first.summary == "数据已校验"
    assert second.status == "completed"
    assert len(created) == 2
    assert runtime.task_store.get(first.task_id)["target_agent"] == "data_agent"


def test_runtime_enforces_invocation_permissions(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(AgentSpec("risk_agent", "risk", lambda: object(), frozenset({"coordinator"})))
    runtime = MultiAgentRuntime(registry, TaskStore(tmp_path / "runtime.db"), RunStore(tmp_path))

    with pytest.raises(PermissionError):
        runtime.invoke("risk_agent", AgentTask("risk", {}), source="backtest_agent")


def test_plan_validator_checks_capabilities_and_orders_dependencies():
    registry = AgentRegistry()
    registry.register(AgentSpec("data_agent", "data", lambda: object(), capabilities=frozenset({"fetch"})))
    registry.register(AgentSpec("backtest_agent", "backtest", lambda: object(), capabilities=frozenset({"run"})))
    plan = ResearchPlan("test", (
        PlanTask("run_backtest", "backtest_agent", "run", "run it", ("fetch_data",)),
        PlanTask("fetch_data", "data_agent", "fetch", "fetch it"),
    ))
    assert [task.plan_task_id for task in PlanValidator(registry).validate(plan)] == ["fetch_data", "run_backtest"]

    invalid = ResearchPlan("bad", (PlanTask("wrong_task", "data_agent", "run", "wrong"),))
    with pytest.raises(ValueError, match="cannot execute"):
        PlanValidator(registry).validate(invalid)


def test_plan_first_coordinator_validates_before_execution(tmp_path: Path):
    plan_json = json.dumps({
        "version": 1,
        "objective": "fetch then test",
        "requires_approval": False,
        "tasks": [
            {"plan_task_id": "fetch_data", "target_agent": "data_agent", "task_type": "fetch",
             "instruction": "fetch data", "depends_on": [], "input_artifact_ids": [], "max_rounds": 2},
            {"plan_task_id": "run_test", "target_agent": "backtest_agent", "task_type": "run",
             "instruction": "run test", "depends_on": ["fetch_data"], "input_artifact_ids": [], "max_rounds": 2},
        ],
    })
    completed = lambda name: Agent(
        ScriptedLLM([LLMResponse(content=json.dumps({
            "status": "completed", "user_message": f"{name} done",
            "reason_codes": [], "evidence_artifact_ids": [],
        }))]), [], tmp_path, tmp_path / "audit.jsonl", name=name, require_structured_final=True,
    )
    registry = AgentRegistry()
    registry.register(AgentSpec("data_agent", "data", lambda: completed("data_agent"), capabilities=frozenset({"fetch"})))
    registry.register(AgentSpec("backtest_agent", "backtest", lambda: completed("backtest_agent"), capabilities=frozenset({"run"})))
    run_store = RunStore(tmp_path); run_id = run_store.create("do research")
    runtime = MultiAgentRuntime(registry, TaskStore(tmp_path / "runtime.db"), run_store)
    coordinator = PlanFirstCoordinator(
        ScriptedLLM([LLMResponse(content=plan_json)]),
        lambda: completed("coordinator"), runtime, registry, run_id=run_id,
    )

    assert coordinator.chat("do research", run_store=run_store, run_id=run_id) == "coordinator done"
    events = run_store.read_events(run_id)
    names = [event["event"] for event in events]
    assert names.index("plan_validated") < names.index("agent_task_started")
    assert names.count("agent_task_started") == 2


def test_plan_requiring_approval_does_not_execute(tmp_path: Path):
    plan_json = json.dumps({
        "version": 1, "objective": "sensitive run", "requires_approval": True,
        "tasks": [{"plan_task_id": "run_test", "target_agent": "backtest_agent", "task_type": "run",
                   "instruction": "run", "depends_on": [], "input_artifact_ids": [], "max_rounds": 2}],
    })
    registry = AgentRegistry()
    registry.register(AgentSpec("backtest_agent", "backtest", lambda: object(), capabilities=frozenset({"run"})))
    run_store = RunStore(tmp_path); run_id = run_store.create("sensitive")
    coordinator = PlanFirstCoordinator(
        ScriptedLLM([LLMResponse(content=plan_json)]), lambda: object(),
        MultiAgentRuntime(registry, TaskStore(tmp_path / "runtime.db"), run_store), registry, run_id=run_id,
    )
    answer = coordinator.chat("sensitive", run_store=run_store, run_id=run_id)
    assert "需要人工批准" in answer
    assert coordinator.last_decision.status == "needs_review"
    assert "agent_task_started" not in [event["event"] for event in run_store.read_events(run_id)]

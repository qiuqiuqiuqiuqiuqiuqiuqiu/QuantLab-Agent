"""Deterministic harness around independently-scoped specialist agent loops."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from ..protocols import AgentResult, AgentTask, ResearchPlan, RunDecision
from .run_store import RunStore
from .task_store import TaskStore


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    factory: Callable[[], object]
    allowed_callers: frozenset[str] = frozenset({"coordinator"})
    capabilities: frozenset[str] = frozenset()


class AgentRegistry:
    def __init__(self):
        self._specs: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Agent already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> AgentSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ValueError(f"Unknown agent: {name}") from exc

    def planning_catalog(self) -> list[dict]:
        return [
            {"agent": spec.name, "description": spec.description, "task_types": sorted(spec.capabilities)}
            for spec in self._specs.values()
        ]


class MultiAgentRuntime:
    """Local transport and durable task boundary.

    Every invocation creates a fresh agent instance, so specialist conversation
    history cannot leak across tasks. A future A2A transport can implement the
    same ``invoke`` contract without changing coordinator tools.
    """

    def __init__(self, registry: AgentRegistry, task_store: TaskStore, run_store: RunStore, artifact_store=None):
        self.registry, self.task_store, self.run_store = registry, task_store, run_store
        self.artifact_store = artifact_store

    def invoke(self, target: str, task: AgentTask, *, source: str = "coordinator") -> AgentResult:
        spec = self.registry.get(target)
        if source not in spec.allowed_callers:
            raise PermissionError(f"{source} is not allowed to invoke {target}")
        if self.artifact_store:
            for artifact_id in task.artifact_ids:
                self.artifact_store.get(artifact_id)
        self.task_store.start(task, source, target)
        self._event(task, "agent_task_started", {"source_agent": source, "target_agent": target})
        try:
            agent = spec.factory()
            requested_rounds = task.payload.get("max_rounds")
            if isinstance(requested_rounds, int) and hasattr(agent, "max_rounds"):
                agent.max_rounds = min(agent.max_rounds, requested_rounds)
            prompt = json.dumps(task.to_dict(), ensure_ascii=False)
            answer = agent.chat(prompt, run_store=self.run_store if task.run_id else None, run_id=task.run_id)
            decision = getattr(agent, "last_decision", None) or RunDecision.from_model_output(answer)
            result = AgentResult(
                task_id=task.task_id,
                status=decision.status if decision else "completed",
                summary=decision.user_message if decision else answer,
                output_artifact_ids=decision.evidence_artifact_ids if decision else (),
                data={"agent": target},
            )
            self.task_store.finish(result)
            self._event(task, "agent_task_completed", {"target_agent": target, "result": result.to_dict()})
            return result
        except Exception as exc:
            self.task_store.fail(task.task_id, str(exc))
            self._event(task, "agent_task_failed", {"target_agent": target, "error": str(exc)})
            raise

    def execute_plan(self, plan: ResearchPlan, *, original_request: str, run_id: str | None = None) -> dict[str, AgentResult]:
        """Execute only tasks declared in a validated plan, in topological order."""
        from .planning import PlanValidator

        ordered = PlanValidator(self.registry).validate(plan)
        results: dict[str, AgentResult] = {}
        for planned in ordered:
            dependency_results = [results[dependency] for dependency in planned.depends_on]
            dependency_artifacts = tuple(
                artifact_id
                for result in dependency_results
                for artifact_id in result.output_artifact_ids
            )
            task = AgentTask(
                task_type=planned.task_type,
                payload={
                    "instruction": planned.instruction,
                    "original_request": original_request,
                    "plan_task_id": planned.plan_task_id,
                    "max_rounds": planned.max_rounds,
                    "dependency_results": [result.to_dict() for result in dependency_results],
                },
                run_id=run_id,
                artifact_ids=tuple(dict.fromkeys((*planned.input_artifact_ids, *dependency_artifacts))),
            )
            result = self.invoke(planned.target_agent, task)
            results[planned.plan_task_id] = result
            if result.status not in {"completed", "validated_pending_approval"}:
                break
        return results

    def _event(self, task: AgentTask, event: str, data: dict) -> None:
        if task.run_id:
            self.run_store.record_event(task.run_id, event, {"task_id": task.task_id, **data})

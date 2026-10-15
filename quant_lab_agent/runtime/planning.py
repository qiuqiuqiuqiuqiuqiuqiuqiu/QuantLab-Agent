"""Strict plan-first validation and execution facade."""
from __future__ import annotations

import json
import re

from ..protocols import ResearchPlan, RunDecision


class PlanValidator:
    def __init__(self, registry, *, max_tasks: int = 12, max_total_rounds: int = 60):
        self.registry, self.max_tasks, self.max_total_rounds = registry, max_tasks, max_total_rounds

    def validate(self, plan: ResearchPlan):
        if plan.version != 1 or not plan.objective:
            raise ValueError("Plan requires version=1 and a non-empty objective")
        if not plan.tasks or len(plan.tasks) > self.max_tasks:
            raise ValueError(f"Plan must contain 1-{self.max_tasks} tasks")
        ids = [task.plan_task_id for task in plan.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Plan task IDs must be unique")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", task_id) for task_id in ids):
            raise ValueError("Plan task IDs must be stable lowercase identifiers")
        if sum(task.max_rounds for task in plan.tasks) > self.max_total_rounds:
            raise ValueError("Plan exceeds the total round budget")

        by_id = {task.plan_task_id: task for task in plan.tasks}
        for task in plan.tasks:
            spec = self.registry.get(task.target_agent)
            if task.task_type not in spec.capabilities:
                raise ValueError(f"{task.target_agent} cannot execute task type {task.task_type}")
            if not task.instruction.strip() or not 1 <= task.max_rounds <= 12:
                raise ValueError(f"Invalid instruction or round budget for {task.plan_task_id}")
            unknown = set(task.depends_on) - set(by_id)
            if unknown or task.plan_task_id in task.depends_on:
                raise ValueError(f"Invalid dependencies for {task.plan_task_id}")

        incoming = {task_id: set(by_id[task_id].depends_on) for task_id in ids}
        ordered = []
        while incoming:
            ready = [task_id for task_id in ids if task_id in incoming and not incoming[task_id]]
            if not ready:
                raise ValueError("Plan dependencies contain a cycle")
            for task_id in ready:
                ordered.append(by_id[task_id])
                incoming.pop(task_id)
                for dependencies in incoming.values():
                    dependencies.discard(task_id)
        return ordered


class PlanFirstCoordinator:
    """Planner -> deterministic validation/execution -> no-tool synthesis."""

    def __init__(self, planner_llm, synthesizer_factory, runtime, registry, *, run_id=None):
        self.planner_llm = planner_llm
        self.synthesizer_factory = synthesizer_factory
        self.runtime, self.registry, self.run_id = runtime, registry, run_id
        self.last_decision: RunDecision | None = None

    def chat(self, user_input: str, on_token=None, on_tool=None, run_store=None, run_id=None) -> str:
        active_run_id = run_id or self.run_id
        catalog = self.registry.planning_catalog()
        planner_prompt = f"""You are QuantLab's planner. Produce the complete execution plan before any action.
You cannot call tools. Return exactly one JSON object with version=1, objective,
requires_approval, and tasks. Every task needs plan_task_id, target_agent,
task_type, instruction, depends_on, input_artifact_ids, and max_rounds.
Use only this capability catalog: {json.dumps(catalog, ensure_ascii=False)}
Do not add work that is not required by the user's request.
"""
        response = self.planner_llm.chat([
            {"role": "system", "content": planner_prompt},
            {"role": "user", "content": user_input},
        ], [])
        plan = ResearchPlan.from_model_output(response.content)
        ordered = PlanValidator(self.registry).validate(plan)
        if run_store and active_run_id:
            run_store.record_event(active_run_id, "plan_created", {"plan": plan.to_dict()})
            run_store.record_event(active_run_id, "plan_validated", {"task_order": [task.plan_task_id for task in ordered]})

        if plan.requires_approval:
            self.last_decision = RunDecision(
                status="needs_review",
                user_message="计划已生成并通过校验，但执行前需要人工批准。",
                reason_codes=("plan_approval_required",),
            )
            if run_store and active_run_id:
                run_store.record_event(active_run_id, "plan_approval_required", {"plan": plan.to_dict()})
            return self.last_decision.user_message

        results = self.runtime.execute_plan(plan, original_request=user_input, run_id=active_run_id)

        if run_store and active_run_id:
            run_store.record_event(active_run_id, "plan_executed", {
                "results": {key: value.to_dict() for key, value in results.items()},
            })
        synthesizer = self.synthesizer_factory()
        synthesis_input = json.dumps({
            "user_request": user_input,
            "validated_plan": plan.to_dict(),
            "task_results": {key: value.to_dict() for key, value in results.items()},
        }, ensure_ascii=False)
        answer = synthesizer.chat(synthesis_input, on_token=on_token, run_store=run_store, run_id=active_run_id)
        self.last_decision = synthesizer.last_decision
        return answer

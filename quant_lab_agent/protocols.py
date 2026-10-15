"""Typed messages exchanged across the QuantLab runtime boundary."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any
from uuid import uuid4


TERMINAL_STATUSES = {
    "completed", "needs_input", "needs_data", "needs_review", "rejected",
    "validated_pending_approval", "failed", "cancelled",
}


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    artifact_type: str
    uri: str
    sha256: str
    schema_version: str = "1"
    created_by: str = "runtime"
    parent_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["parent_ids"] = list(self.parent_ids)
        return value


@dataclass(frozen=True)
class AgentTask:
    task_type: str
    payload: dict[str, Any]
    run_id: str | None = None
    task_id: str = field(default_factory=lambda: f"task_{uuid4().hex}")
    parent_task_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    correlation_id: str = field(default_factory=lambda: f"corr_{uuid4().hex}")
    schema_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artifact_ids"] = list(self.artifact_ids)
        return value


@dataclass(frozen=True)
class PlanTask:
    plan_task_id: str
    target_agent: str
    task_type: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    max_rounds: int = 8

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["depends_on"] = list(self.depends_on)
        value["input_artifact_ids"] = list(self.input_artifact_ids)
        return value


@dataclass(frozen=True)
class ResearchPlan:
    objective: str
    tasks: tuple[PlanTask, ...]
    version: int = 1
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "objective": self.objective,
            "requires_approval": self.requires_approval,
            "tasks": [task.to_dict() for task in self.tasks],
        }

    @classmethod
    def from_model_output(cls, text: str) -> "ResearchPlan":
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Planner output must be one JSON object") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            raise ValueError("Planner output must contain a tasks array")
        tasks = []
        for raw in payload["tasks"]:
            if not isinstance(raw, dict):
                raise ValueError("Each planned task must be an object")
            depends_on = raw.get("depends_on", [])
            artifact_ids = raw.get("input_artifact_ids", [])
            max_rounds = raw.get("max_rounds", 8)
            if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
                raise ValueError("depends_on must be an array of task IDs")
            if not isinstance(artifact_ids, list) or not all(isinstance(item, str) for item in artifact_ids):
                raise ValueError("input_artifact_ids must be an array of artifact IDs")
            if isinstance(max_rounds, bool) or not isinstance(max_rounds, int):
                raise ValueError("max_rounds must be an integer")
            try:
                tasks.append(PlanTask(
                    plan_task_id=str(raw["plan_task_id"]),
                    target_agent=str(raw["target_agent"]),
                    task_type=str(raw["task_type"]),
                    instruction=str(raw["instruction"]),
                    depends_on=tuple(depends_on),
                    input_artifact_ids=tuple(artifact_ids),
                    max_rounds=max_rounds,
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid planned task: {raw}") from exc
        version = payload.get("version", 1)
        requires_approval = payload.get("requires_approval", False)
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("Plan version must be an integer")
        if not isinstance(requires_approval, bool):
            raise ValueError("requires_approval must be a boolean")
        return cls(
            objective=str(payload.get("objective", "")).strip(),
            tasks=tuple(tasks),
            version=version,
            requires_approval=requires_approval,
        )


@dataclass(frozen=True)
class AgentResult:
    task_id: str
    status: str
    summary: str
    output_artifact_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError(f"Invalid agent result status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["output_artifact_ids"] = list(self.output_artifact_ids)
        value["warnings"] = list(self.warnings)
        return value


@dataclass(frozen=True)
class RunDecision:
    status: str
    user_message: str
    reason_codes: tuple[str, ...] = ()
    evidence_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError(f"Invalid run decision status: {self.status}")
        if not self.user_message.strip():
            raise ValueError("user_message must not be empty")

    @classmethod
    def from_model_output(cls, text: str) -> "RunDecision | None":
        """Parse a decision without guessing status from prose."""
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or "status" not in payload or "user_message" not in payload:
            return None
        try:
            return cls(
                status=str(payload["status"]),
                user_message=str(payload["user_message"]),
                reason_codes=tuple(map(str, payload.get("reason_codes", []))),
                evidence_artifact_ids=tuple(map(str, payload.get("evidence_artifact_ids", []))),
            )
        except (TypeError, ValueError):
            return None

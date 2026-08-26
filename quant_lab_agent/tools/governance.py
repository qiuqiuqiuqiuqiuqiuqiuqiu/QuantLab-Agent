"""Governance gates are deterministic and explicit, never delegated to model judgement."""
import json
from pathlib import Path
from .base import Tool

class ValidateResearchTool(Tool):
    name = "validate_research"
    description = "Run deterministic governance checks against a registered research record and supplied metrics."
    parameters = {"type":"object","properties": {"research_id":{"type":"string"},"metrics":{"type":"object"}},"required":["research_id","metrics"]}
    def __init__(self, workspace: Path): self.workspace = workspace
    def execute(self, research_id: str, metrics: dict) -> str:
        path = self.workspace / ".quant-agent" / "research.json"
        records = json.loads(path.read_text()) if path.exists() else {}
        record, failures = records.get(research_id), []
        if not record: failures.append("research record is missing")
        elif not all(record.get(key) for key in ("data_sources", "methodology", "reproduce_command")): failures.append("data lineage, methodology, or reproducibility is incomplete")
        for key in ("sharpe", "max_drawdown", "turnover"):
            if key not in metrics: failures.append(f"required metric missing: {key}")
        if metrics.get("max_drawdown", 0) > 0.20: failures.append("max_drawdown exceeds 20% guardrail")
        if metrics.get("turnover", 0) > 5: failures.append("turnover exceeds 5x guardrail")
        decision = "needs_review" if failures else "validated_pending_approval"
        return json.dumps({"research_id": research_id, "decision": decision, "failures": failures, "note": "Validation is not deployment approval."}, ensure_ascii=False)

class RequestApprovalTool(Tool):
    name = "request_approval"
    description = "Create a human approval request. It never deploys a strategy."
    parameters = {"type":"object","properties": {"research_id":{"type":"string"},"risk_owner":{"type":"string"},"summary":{"type":"string"}},"required":["research_id","risk_owner","summary"]}
    def __init__(self, workspace: Path): self.path = workspace / ".quant-agent" / "approval_requests.jsonl"
    def execute(self, **request):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        request["status"] = "pending_human_approval"
        with self.path.open("a", encoding="utf-8") as out: out.write(json.dumps(request, ensure_ascii=False) + "\n")
        return f"Approval request created for {request['research_id']}; status=pending_human_approval."

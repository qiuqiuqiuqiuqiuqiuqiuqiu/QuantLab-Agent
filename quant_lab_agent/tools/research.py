"""Research evidence tools. They persist structured metadata rather than untracked prose."""
import json
from pathlib import Path
from .base import Tool

class RegisterResearchTool(Tool):
    name = "register_research"
    description = "Register a strategy hypothesis, data lineage, methodology, and reproducibility command."
    parameters = {"type":"object","properties": {"research_id":{"type":"string"},"hypothesis":{"type":"string"},"data_sources":{"type":"array","items":{"type":"string"}},"methodology":{"type":"string"},"reproduce_command":{"type":"string"}},"required":["research_id","hypothesis","data_sources","methodology","reproduce_command"]}
    def __init__(self, workspace: Path): self.path = workspace / ".quant-agent" / "research.json"
    def execute(self, **record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = json.loads(self.path.read_text()) if self.path.exists() else {}
        records[record["research_id"]] = record
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        return f"Registered research '{record['research_id']}' with {len(record['data_sources'])} data sources."

class RecordBacktestTool(Tool):
    name = "record_backtest"
    description = "Record backtest metrics and caveats as evidence; this does not approve deployment."
    parameters = {"type":"object","properties": {"research_id":{"type":"string"},"period":{"type":"string"},"metrics":{"type":"object"},"assumptions":{"type":"array","items":{"type":"string"}},"limitations":{"type":"array","items":{"type":"string"}}},"required":["research_id","period","metrics","assumptions","limitations"]}
    def __init__(self, workspace: Path): self.path = workspace / ".quant-agent" / "backtests.jsonl"
    def execute(self, **record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as out: out.write(json.dumps(record, ensure_ascii=False) + "\n")
        return f"Backtest evidence recorded for {record['research_id']} ({record['period']})."

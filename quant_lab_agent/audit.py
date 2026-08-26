"""Append-only JSONL audit trail for every material agent action."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

class AuditLog:
    def __init__(self, path: Path): self.path = path
    def record(self, event: str, **data) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        item = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")

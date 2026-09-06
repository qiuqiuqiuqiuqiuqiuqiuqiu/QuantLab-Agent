"""Small, dependency-free persistence layer for resumable agent executions."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Stores each run independently under ``.quant-agent/runs``.

    A manifest is intentionally plain JSON so a run can be inspected or replayed
    without a database or agent framework.
    """

    def __init__(self, workspace: Path):
        self.root = workspace / ".quant-agent" / "runs"

    def create(self, prompt: str) -> str:
        run_id = f"run_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        self._write_manifest(run_id, {
            "run_id": run_id,
            "status": "running",
            "prompt": prompt,
            "created_at": _now(),
            "updated_at": _now(),
            "final_answer": None,
        })
        self.record_event(run_id, "run_started", {"prompt": prompt})
        return run_id

    def load(self, run_id: str) -> dict:
        self._validate_id(run_id)
        path = self.root / run_id / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Run not found: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def finish(self, run_id: str, answer: str, status: str = "completed") -> None:
        manifest = self.load(run_id)
        manifest.update({"status": status, "final_answer": answer, "updated_at": _now()})
        self._write_manifest(run_id, manifest)
        self.record_event(run_id, "run_finished", {"status": status, "answer": answer})

    def annotate(self, run_id: str, **metadata) -> None:
        """Attach non-execution metadata such as a parent research project."""
        manifest = self.load(run_id)
        manifest.update(metadata)
        manifest["updated_at"] = _now()
        self._write_manifest(run_id, manifest)

    def record_event(self, run_id: str, event: str, data: dict) -> None:
        self._validate_id(run_id)
        path = self.root / run_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": _now(), "event": event, **data}, ensure_ascii=False, default=str) + "\n")

    def events_path(self, run_id: str) -> Path:
        self._validate_id(run_id)
        return self.root / run_id / "events.jsonl"

    def read_events(self, run_id: str) -> list[dict]:
        path = self.events_path(run_id)
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def list_runs(self) -> list[dict]:
        if not self.root.exists():
            return []
        runs = []
        for path in self.root.glob("*/manifest.json"):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        return sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)

    def _write_manifest(self, run_id: str, manifest: dict) -> None:
        self._validate_id(run_id)
        path = self.root / run_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _validate_id(run_id: str) -> None:
        if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
            raise ValueError("Invalid run id")

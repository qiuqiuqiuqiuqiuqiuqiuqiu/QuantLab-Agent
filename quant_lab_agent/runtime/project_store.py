"""Small local registry for research programmes and their experiment runs."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    """Persists project metadata separately from execution runs.

    A project is a research container (thesis, universe, benchmark); a run is a
    reproducible experiment inside that container. Keeping these separate is
    what prevents the product from becoming a one-question report generator.
    """
    def __init__(self, workspace: Path):
        self.path = workspace / ".quant-agent" / "projects.json"

    def list(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
            return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)
        except json.JSONDecodeError:
            return []

    def create(self, name: str, thesis: str, universe: list[str], benchmark: str, owner: str) -> dict:
        project = {
            "project_id": "project_" + uuid4().hex[:10], "name": name, "thesis": thesis,
            "universe": universe, "benchmark": benchmark, "owner": owner,
            "created_at": _now(), "updated_at": _now(),
        }
        items = self.list(); items.append(project); self._write(items)
        return project

    def get(self, project_id: str) -> dict:
        for project in self.list():
            if project.get("project_id") == project_id:
                return project
        raise FileNotFoundError(f"Project not found: {project_id}")

    def touch(self, project_id: str) -> None:
        items = self.list()
        for item in items:
            if item.get("project_id") == project_id:
                item["updated_at"] = _now()
        self._write(items)

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

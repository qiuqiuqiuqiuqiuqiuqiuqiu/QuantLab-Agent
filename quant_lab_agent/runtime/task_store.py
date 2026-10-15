"""SQLite task ledger for resumable and auditable specialist-agent invocations."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..protocols import AgentResult, AgentTask


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS agent_tasks (
              task_id TEXT PRIMARY KEY,
              run_id TEXT,
              parent_task_id TEXT,
              correlation_id TEXT NOT NULL,
              source_agent TEXT NOT NULL,
              target_agent TEXT NOT NULL,
              task_type TEXT NOT NULL,
              status TEXT NOT NULL,
              input_json TEXT NOT NULL,
              output_json TEXT,
              attempt INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_run ON agent_tasks(run_id, created_at);
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def start(self, task: AgentTask, source_agent: str, target_agent: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_tasks(task_id,run_id,parent_task_id,correlation_id,source_agent,target_agent,task_type,status,input_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (task.task_id, task.run_id, task.parent_task_id, task.correlation_id, source_agent,
                 target_agent, task.task_type, "running", json.dumps(task.to_dict(), ensure_ascii=False), now, now),
            )

    def finish(self, result: AgentResult) -> None:
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE agent_tasks SET status=?, output_json=?, updated_at=? WHERE task_id=? AND status='running'",
                (result.status, json.dumps(result.to_dict(), ensure_ascii=False), _now(), result.task_id),
            ).rowcount
        if updated != 1:
            raise ValueError(f"Task is missing or already terminal: {result.task_id}")

    def fail(self, task_id: str, message: str) -> None:
        result = AgentResult(task_id=task_id, status="failed", summary=message)
        self.finish(result)

    def get(self, task_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"Task not found: {task_id}")
        value = dict(row)
        value["input"] = json.loads(value.pop("input_json"))
        value["output"] = json.loads(value.pop("output_json")) if value.get("output_json") else None
        return value


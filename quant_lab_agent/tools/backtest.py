"""A deliberately constrained Python backtest runner.

This is a process boundary and safety guardrail, not a security sandbox. Deployments
handling untrusted strategy code must use containers/VMs with network isolation.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import subprocess
import sys
from pathlib import Path

from .base import Tool
from .workspace import WorkspacePathMixin


class RunBacktestTool(WorkspacePathMixin, Tool):
    name = "run_backtest"
    description = "Run a workspace-local Python backtest without a shell, then automatically load its JSON result artifact. On failure, return a structured diagnosis so the script can be repaired and rerun."
    parameters = {
        "type": "object",
        "properties": {
            "script_path": {"type": "string", "description": "Workspace-relative Python script"},
            "result_path": {"type": "string", "description": "Workspace-relative JSON artifact produced by the script"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
        },
        "required": ["script_path", "result_path"],
    }

    def __init__(self, workspace: Path, default_timeout_seconds: int = 120):
        super().__init__(workspace)
        self.default_timeout_seconds = default_timeout_seconds

    def execute(self, script_path: str, result_path: str, timeout_seconds: int | None = None, environment: dict[str, str] | None = None) -> str:
        try:
            script = self.resolve_path(script_path)
            result = self.resolve_path(result_path)
        except ValueError as exc:
            return self._failure("invalid_path", str(exc))
        if script.suffix != ".py" or not script.is_file():
            return self._failure("invalid_script", f"script_path must name an existing .py file: {script_path}")
        # A stale artifact must never be mistaken for the output of this invocation.
        if result.exists():
            if not result.is_file():
                return self._failure("invalid_result_path", f"result_path is not a file: {result_path}")
            result.unlink()
        timeout = min(timeout_seconds or self.default_timeout_seconds, 300)
        try:
            safe_env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
            if environment:
                # This optional channel is reserved for trusted server-side jobs. The
                # public tool schema intentionally does not expose arbitrary env vars.
                safe_env.update({key: value for key, value in environment.items() if key in {"QUANTLAB_JOB_FILE", "QUANTLAB_RESULT_FILE"}})
            completed = subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=self.workspace,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                # Keep invocation deterministic and avoid inheriting credentials.
                env=safe_env,
            )
        except subprocess.TimeoutExpired as exc:
            return self._failure("timeout", f"Backtest exceeded {timeout}s", stdout=exc.stdout, stderr=exc.stderr)
        except OSError as exc:
            return self._failure("runner_error", str(exc))

        if completed.returncode != 0:
            return self._failure(
                self._classify(completed.stderr, completed.stdout),
                f"Python exited with code {completed.returncode}",
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        if not result.is_file():
            return self._failure("missing_result", f"Backtest succeeded but did not create {result_path}", stdout=completed.stdout)
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._failure("invalid_result", f"Cannot parse {result_path} as JSON: {exc}", stdout=completed.stdout)
        if not isinstance(payload, dict):
            return self._failure("invalid_result", "Result JSON must be an object", stdout=completed.stdout)
        metrics = payload.get("metrics", payload)
        if not isinstance(metrics, dict):
            return self._failure("invalid_result", "Result field 'metrics' must be an object", stdout=completed.stdout)
        schema_error = self._validate_metrics(metrics)
        if schema_error:
            return self._failure("invalid_result", schema_error, stdout=completed.stdout)
        artifacts = self._extract_artifacts(payload)
        if isinstance(artifacts, str):
            return self._failure("invalid_result", artifacts, stdout=completed.stdout)
        artifact_sha256 = hashlib.sha256(result.read_bytes()).hexdigest()
        return json.dumps({
            "status": "success", "script_path": script_path, "result_path": result_path,
            "metrics": metrics, "artifacts": artifacts, "artifact_sha256": artifact_sha256, "stdout": self._clip(completed.stdout),
            "next_action": "Use record_backtest to persist these metrics, then validate_research.",
        }, ensure_ascii=False)

    def _failure(self, category: str, message: str, stdout=None, stderr=None) -> str:
        return json.dumps({
            "status": "failed", "failure_category": category, "message": message,
            "stdout": self._clip(stdout), "stderr": self._clip(stderr),
            "repair_hint": self._repair_hint(category),
            "next_action": "Read the script, make one targeted repair with replace_workspace_text, then rerun run_backtest. Do not claim a result until status is success.",
        }, ensure_ascii=False)

    @staticmethod
    def _validate_metrics(metrics: dict) -> str | None:
        required = ("sharpe", "max_drawdown", "turnover")
        missing = [key for key in required if key not in metrics]
        if missing:
            return "Result metrics missing required fields: " + ", ".join(missing)
        for key in required:
            value = metrics[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return f"Result metric '{key}' must be a finite number"
        if metrics["max_drawdown"] < 0:
            return "Result metric 'max_drawdown' must be non-negative"
        if metrics["turnover"] < 0:
            return "Result metric 'turnover' must be non-negative"
        return None

    @staticmethod
    def _extract_artifacts(payload: dict) -> dict | str:
        """Validate optional visualization artifacts without accepting arbitrary blobs."""
        artifacts = {}
        for name in ("equity_curve", "drawdown_curve"):
            if name not in payload:
                continue
            curve = payload[name]
            if not isinstance(curve, list) or len(curve) > 10_000:
                return f"Artifact '{name}' must be a list with at most 10000 points"
            normalized = []
            for index, point in enumerate(curve):
                if not isinstance(point, list) or len(point) != 2 or not isinstance(point[0], str):
                    return f"Artifact '{name}' point {index} must be [date_string, numeric_value]"
                value = point[1]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    return f"Artifact '{name}' point {index} has an invalid numeric value"
                normalized.append([point[0], value])
            artifacts[name] = normalized
        return artifacts

    @staticmethod
    def _classify(stderr: str, stdout: str) -> str:
        text = f"{stderr}\n{stdout}"
        if "SyntaxError" in text or "IndentationError" in text:
            return "syntax_error"
        if "ModuleNotFoundError" in text or "ImportError" in text:
            return "missing_dependency"
        if "FileNotFoundError" in text or "KeyError" in text:
            return "data_or_schema_error"
        return "runtime_error"

    @staticmethod
    def _repair_hint(category: str) -> str:
        return {
            "syntax_error": "Inspect the traceback line and repair Python syntax before rerunning.",
            "missing_dependency": "Do not install packages automatically; use available dependencies or request human setup.",
            "data_or_schema_error": "Inspect input paths and expected data columns/schema.",
            "missing_result": "Ensure the script writes the requested JSON artifact on its success path.",
            "invalid_result": "Write a JSON object with numeric metrics, optionally under a 'metrics' key.",
            "timeout": "Reduce work, use a smaller sample, or ask for a larger reviewed timeout.",
        }.get(category, "Inspect the traceback, apply one minimal repair, and rerun.")

    @staticmethod
    def _clip(value: str | bytes | None, limit: int = 4000) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return value if len(value) <= limit else value[:limit] + "\n…[truncated]"

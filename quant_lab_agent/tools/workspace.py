"""Workspace-scoped file tools used to inspect and repair research scripts."""
from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from .base import Tool


class WorkspacePathMixin:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def resolve_path(self, relative_path: str) -> Path:
        candidate = (self.workspace / relative_path).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Path must stay inside the configured workspace") from exc
        return candidate


class ReadWorkspaceFileTool(WorkspacePathMixin, Tool):
    name = "read_workspace_file"
    description = "Read a UTF-8 text file inside the workspace. Use this to inspect a failed research script before repairing it."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path"},
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 30000},
        },
        "required": ["path"],
    }

    def execute(self, path: str, max_chars: int = 12000) -> str:
        file_path = self.resolve_path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Not a readable file: {path}")
        text = file_path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return text


class ReplaceWorkspaceTextTool(WorkspacePathMixin, Tool):
    """Safe edit primitive: replacement is rejected unless the anchor is unique."""

    name = "replace_workspace_text"
    description = "Replace one uniquely occurring text block in a workspace file and return a unified diff. Use after reading the file; never guess line numbers."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path"},
            "old_text": {"type": "string", "description": "Exact, unique text to replace"},
            "new_text": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def execute(self, path: str, old_text: str, new_text: str) -> str:
        if not old_text:
            raise ValueError("old_text must not be empty")
        file_path = self.resolve_path(path)
        original = file_path.read_text(encoding="utf-8")
        occurrences = original.count(old_text)
        if occurrences != 1:
            raise ValueError(f"Replacement anchor must occur exactly once; found {occurrences}")
        updated = original.replace(old_text, new_text, 1)
        file_path.write_text(updated, encoding="utf-8")
        diff = "".join(unified_diff(
            original.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=path, tofile=path,
        ))
        return f"Updated {path}.\n{diff}"

"""Controlled just-in-time access to immutable runtime artifacts."""
from __future__ import annotations

import json

from .base import Tool


class ReadArtifactTool(Tool):
    name = "read_artifact"
    description = "Read verified artifact metadata and a bounded text preview by artifact_id."
    parameters = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "pattern": "^art_[A-Za-z0-9_.-]+$"},
        },
        "required": ["artifact_id"],
        "additionalProperties": False,
    }
    cacheable = True

    def __init__(self, artifact_store, preview_limit: int = 6000):
        self.artifact_store, self.preview_limit = artifact_store, preview_limit

    def execute(self, artifact_id: str) -> str:
        ref = self.artifact_store.get(artifact_id)
        data = self.artifact_store.read_bytes(artifact_id)
        try:
            preview = data.decode("utf-8")
        except UnicodeDecodeError:
            preview = "[binary artifact; preview unavailable]"
        if len(preview) > self.preview_limit:
            preview = preview[:self.preview_limit] + "\n…[artifact preview truncated]"
        return json.dumps({"artifact": ref.to_dict(), "preview": preview}, ensure_ascii=False)

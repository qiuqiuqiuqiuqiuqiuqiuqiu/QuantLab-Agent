"""Immutable, content-addressed artifacts shared by agents and deterministic tools."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ..protocols import ArtifactRef


class ArtifactStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = self.workspace / ".quant-agent" / "artifacts"

    def put_bytes(
        self, data: bytes, *, artifact_type: str, suffix: str = ".bin",
        created_by: str = "runtime", parent_ids: tuple[str, ...] = (), schema_version: str = "1",
    ) -> ArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        safe_type = re.sub(r"[^A-Za-z0-9_.-]+", "-", artifact_type).strip("-") or "artifact"
        artifact_id = f"art_{safe_type}_{digest[:20]}"
        directory = self.root / artifact_id
        payload_path = directory / f"payload{suffix if suffix.startswith('.') else '.' + suffix}"
        metadata_path = directory / "metadata.json"
        directory.mkdir(parents=True, exist_ok=True)

        # Content addressing makes retries idempotent. Atomic replace prevents readers
        # from observing partially-written payloads.
        if not payload_path.exists():
            fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".payload-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, payload_path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

        ref = ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            uri=f"artifact://{artifact_id}/{payload_path.name}",
            sha256=digest,
            schema_version=schema_version,
            created_by=created_by,
            parent_ids=parent_ids,
        )
        if not metadata_path.exists():
            metadata_path.write_text(json.dumps(ref.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ref

    def put_file(self, path: Path, **metadata) -> ArtifactRef:
        resolved = path.resolve()
        if self.workspace not in resolved.parents:
            raise ValueError("Artifact source must be inside the workspace")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        return self.put_bytes(resolved.read_bytes(), suffix=resolved.suffix or ".bin", **metadata)

    def get(self, artifact_id: str) -> ArtifactRef:
        self._validate_id(artifact_id)
        path = self.root / artifact_id / "metadata.json"
        if not path.is_file():
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["parent_ids"] = tuple(payload.get("parent_ids", ()))
        return ArtifactRef(**payload)

    def read_bytes(self, artifact_id: str) -> bytes:
        ref = self.get(artifact_id)
        filename = ref.uri.rsplit("/", 1)[-1]
        data = (self.root / artifact_id / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError(f"Artifact integrity check failed: {artifact_id}")
        return data

    @staticmethod
    def _validate_id(artifact_id: str) -> None:
        if not re.fullmatch(r"art_[A-Za-z0-9_.-]+", artifact_id):
            raise ValueError("Invalid artifact id")


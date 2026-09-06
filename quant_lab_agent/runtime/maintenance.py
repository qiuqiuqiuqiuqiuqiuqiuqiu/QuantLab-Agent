"""Safe local retention utilities for QuantLab runtime artifacts.

Destructive actions are deliberately opt-in. Archive creation is verified before
an original run directory is removed, and data uploads are never deleted here.
"""
from __future__ import annotations

import json
import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

TERMINAL_STATUSES = {"completed", "failed", "needs_review", "needs_input", "needs_data"}


@dataclass(frozen=True)
class ArchiveCandidate:
    run_id: str
    source: Path
    archive: Path
    bytes: int
    updated_at: datetime


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def archive_candidates(workspace: Path, older_than_days: int, now: datetime | None = None) -> list[ArchiveCandidate]:
    """Find terminal runs eligible for archival without modifying anything."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    root = workspace / ".quant-agent" / "runs"
    archive_root = workspace / ".quant-agent" / "archive" / "runs"
    candidates: list[ArchiveCandidate] = []
    if not root.exists():
        return candidates
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        updated_at = _parse_time(manifest.get("updated_at") or manifest.get("created_at"))
        run_dir = manifest_path.parent
        if manifest.get("status") not in TERMINAL_STATUSES or not updated_at or updated_at >= cutoff:
            continue
        archive = archive_root / updated_at.strftime("%Y-%m") / f"{run_dir.name}.tar.gz"
        if archive.exists():
            continue
        candidates.append(ArchiveCandidate(run_dir.name, run_dir, archive, _directory_size(run_dir), updated_at))
    return sorted(candidates, key=lambda item: item.updated_at)


def archive_runs(workspace: Path, older_than_days: int, execute: bool = False) -> dict:
    """Preview or archive terminal run folders. Never deletes uploads/artifacts."""
    candidates = archive_candidates(workspace, older_than_days)
    report = {"mode": "execute" if execute else "dry_run", "older_than_days": older_than_days,
              "runs": [{"run_id": item.run_id, "bytes": item.bytes, "archive": str(item.archive.relative_to(workspace))} for item in candidates],
              "archived_count": 0, "archived_bytes": 0}
    if not execute:
        return report
    for item in candidates:
        item.archive.parent.mkdir(parents=True, exist_ok=True)
        temp_archive = item.archive.with_suffix(item.archive.suffix + ".tmp")
        with tarfile.open(temp_archive, "w:gz") as archive:
            archive.add(item.source, arcname=item.source.name)
        # Verify both the archive and essential run manifest before deleting source.
        with tarfile.open(temp_archive, "r:gz") as archive:
            expected = f"{item.source.name}/manifest.json"
            if expected not in archive.getnames():
                raise RuntimeError(f"Archive verification failed for {item.run_id}")
        temp_archive.replace(item.archive)
        shutil.rmtree(item.source)
        report["archived_count"] += 1
        report["archived_bytes"] += item.bytes
    return report


def rotate_audit_log(audit_log: Path, max_bytes: int, execute: bool = False, now: datetime | None = None) -> dict:
    """Rotate a JSONL audit file once it reaches a size threshold.

    Rotation is an atomic rename on the same filesystem; no event data is
    rewritten or discarded. The next audit write creates a fresh active file.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    size = audit_log.stat().st_size if audit_log.exists() else 0
    report = {"mode": "execute" if execute else "dry_run", "active_log": str(audit_log), "bytes": size,
              "max_bytes": max_bytes, "rotation_needed": size >= max_bytes, "rotated_to": None}
    if not report["rotation_needed"] or not execute:
        return report
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    rotated = audit_log.with_name(f"{audit_log.stem}.{timestamp}{audit_log.suffix}")
    suffix = 1
    while rotated.exists():
        rotated = audit_log.with_name(f"{audit_log.stem}.{timestamp}.{suffix}{audit_log.suffix}")
        suffix += 1
    audit_log.replace(rotated)
    report["rotated_to"] = str(rotated)
    return report

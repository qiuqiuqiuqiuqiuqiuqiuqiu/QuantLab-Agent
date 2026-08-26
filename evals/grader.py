"""Small deterministic grader for tool-level evaluation cases."""
from __future__ import annotations


def grade(case: dict, actual: dict) -> dict:
    mismatches = []
    if actual.get("status") != case["expected_status"]:
        mismatches.append(f"status: expected {case['expected_status']}, got {actual.get('status')}")
    expected_category = case.get("expected_failure_category")
    if expected_category and actual.get("failure_category") != expected_category:
        mismatches.append(
            f"failure_category: expected {expected_category}, got {actual.get('failure_category')}"
        )
    return {"id": case["id"], "passed": not mismatches, "mismatches": mismatches}

"""Run deterministic backtest-runner evals without an API key or Agent framework."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Allow `python evals/runner.py` from the repository root without installation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant_lab_agent.tools.backtest import RunBacktestTool  # noqa: E402
from evals.grader import grade  # noqa: E402


def run(cases_file: Path = ROOT / "evals" / "cases.jsonl") -> dict:
    cases = [json.loads(line) for line in cases_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Cases intentionally write result artifacts.  Run copies in a disposable
    # workspace so evals neither mutate tracked fixtures nor depend on a
    # writable repository checkout.
    with tempfile.TemporaryDirectory(prefix="quantlab-evals-") as temporary:
        workspace = Path(temporary)
        shutil.copytree(ROOT / "evals" / "cases", workspace / "evals" / "cases")
        tool = RunBacktestTool(workspace)
        results = []
        for case in cases:
            result = json.loads(tool.execute(case["script_path"], case["result_path"]))
            results.append({"case": grade(case, result), "actual": result})
    passed = sum(item["case"]["passed"] for item in results)
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QuantLab deterministic backtest evals")
    parser.add_argument("--json", action="store_true", help="Print full JSON results")
    args = parser.parse_args()
    summary = run()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"Backtest evals: {summary['passed']}/{summary['total']} passed")
        for item in summary["results"]:
            marker = "PASS" if item["case"]["passed"] else "FAIL"
            print(f"[{marker}] {item['case']['id']} {'; '.join(item['case']['mismatches'])}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

from pathlib import Path
Path("evals/cases/invalid_result/result.json").write_text('{"metrics": {"sharpe": 1.0}}', encoding="utf-8")

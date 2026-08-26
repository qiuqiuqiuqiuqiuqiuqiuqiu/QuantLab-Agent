import json
from pathlib import Path

from quant_lab_agent.tools.backtest import RunBacktestTool


def test_runner_loads_json_metrics_artifact(tmp_path: Path):
    (tmp_path / "backtest.py").write_text(
        """import json
from pathlib import Path
Path('artifacts').mkdir(exist_ok=True)
Path('artifacts/result.json').write_text(json.dumps({'metrics': {'sharpe': 1.25, 'max_drawdown': 0.12, 'turnover': 1.2}}))
""",
        encoding="utf-8",
    )
    result = json.loads(RunBacktestTool(tmp_path).execute("backtest.py", "artifacts/result.json"))
    assert result["status"] == "success"
    assert result["metrics"]["sharpe"] == 1.25


def test_runner_returns_diagnostic_for_script_failure(tmp_path: Path):
    (tmp_path / "broken.py").write_text("raise FileNotFoundError('prices.parquet')\n", encoding="utf-8")
    result = json.loads(RunBacktestTool(tmp_path).execute("broken.py", "result.json"))
    assert result["status"] == "failed"
    assert result["failure_category"] == "data_or_schema_error"
    assert "repair_hint" in result


def test_runner_rejects_path_traversal(tmp_path: Path):
    result = json.loads(RunBacktestTool(tmp_path).execute("../outside.py", "result.json"))
    assert result["status"] == "failed"
    assert result["failure_category"] == "invalid_path"


def test_runner_rejects_missing_required_metrics(tmp_path: Path):
    (tmp_path / "partial.py").write_text(
        "from pathlib import Path; Path('result.json').write_text('{\"metrics\": {\"sharpe\": 1.0}}')",
        encoding="utf-8",
    )
    result = json.loads(RunBacktestTool(tmp_path).execute("partial.py", "result.json"))
    assert result["failure_category"] == "invalid_result"
    assert "max_drawdown" in result["message"]


def test_runner_returns_optional_curve_artifacts(tmp_path: Path):
    (tmp_path / "curve.py").write_text(
        """import json
from pathlib import Path
Path('result.json').write_text(json.dumps({
  'metrics': {'sharpe': 1.0, 'max_drawdown': 0.1, 'turnover': 1.0},
  'equity_curve': [['2025-01-01', 1.0], ['2025-01-02', 1.02]],
  'drawdown_curve': [['2025-01-01', 0.0], ['2025-01-02', -0.01]]
}))
""", encoding="utf-8"
    )
    result = json.loads(RunBacktestTool(tmp_path).execute("curve.py", "result.json"))
    assert result["artifacts"]["equity_curve"][1][1] == 1.02

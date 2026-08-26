import json
from pathlib import Path
Path("evals/cases/successful_backtest/result.json").write_text(json.dumps({
    "metrics": {"sharpe": 1.15, "max_drawdown": 0.18, "turnover": 1.4}
}), encoding="utf-8")

import json
from pathlib import Path

from quant_lab_agent.tools.backtest import RunBacktestTool
from quant_lab_agent.tools.strategy_template import CreateStrategyTemplateTool


def write_prices(path: Path) -> None:
    prices = [100, 101, 99, 102, 105, 103, 107, 109, 106, 110, 112, 111, 115, 118, 116, 120, 122, 124, 123, 126, 128, 130, 129, 132, 134, 136, 138, 140, 139, 142, 144, 146, 148, 150, 151]
    path.write_text("date,open,high,low,close,volume\n" + "\n".join(f"2025-01-{{i+1:02d}},1,1,1,{{price}},100" for i, price in enumerate(prices)), encoding="utf-8")


def test_compiles_a_declared_strategy_and_runs_it(tmp_path: Path):
    csv_path = tmp_path / "prices.csv"; write_prices(csv_path)
    created = json.loads(CreateStrategyTemplateTool(tmp_path).execute(
        research_id="nvda-ma-v1", ticker="NVDA", price_data_path="prices.csv", strategy_type="moving_average", fast_window=3, slow_window=8,
    ))
    spec = json.loads((tmp_path / created["strategy_spec_path"]).read_text())
    assert spec["strategy_type"] == "moving_average"
    assert "same-bar look-ahead" in spec["execution_model"]
    result = json.loads(RunBacktestTool(tmp_path).execute(created["script_path"], created["result_path"]))
    assert result["status"] == "success"
    assert "annualized_return" in result["metrics"]
    assert (tmp_path / created["result_path"]).exists()


def test_rejects_unapproved_or_inconsistent_template_parameters(tmp_path: Path):
    csv_path = tmp_path / "prices.csv"; write_prices(csv_path)
    tool = CreateStrategyTemplateTool(tmp_path)
    try:
        tool.execute("nvda-x", "NVDA", "prices.csv", "moving_average", fast_window=30, slow_window=10)
    except ValueError as exc:
        assert "fast_window" in str(exc)
    else:
        raise AssertionError("invalid moving-average parameters should fail")

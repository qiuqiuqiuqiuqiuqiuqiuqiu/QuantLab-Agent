import json
from datetime import date, timedelta
from pathlib import Path

from quant_lab_agent.tools.forecast import ForecastMarketDirectionTool


def test_forecast_writes_an_auditable_probability_artifact(tmp_path: Path):
    rows = ["date,close"]
    price = 100.0
    start = date(2025, 1, 1)
    for day in range(1, 91):
        price *= 1.004 if day % 3 else 0.992
        rows.append(f"{start + timedelta(days=day)},{price}")
    (tmp_path / "prices.csv").write_text("\n".join(rows), encoding="utf-8")
    result = json.loads(ForecastMarketDirectionTool(tmp_path).execute("nvda-weekly-v1", "NVDA", "prices.csv", horizon_bars=2))
    assert result["status"] == "success"
    assert 0 <= result["forecast"]["probability_up"] <= 1
    assert result["forecast"]["confidence"] in {"low", "medium"}
    saved = json.loads((tmp_path / result["artifact_path"]).read_text())
    assert saved["model"]["name"] == "ar1_log_return_scenario"
    assert "not a trading recommendation" in saved["limitations"][-1]


def test_forecast_rejects_too_little_history(tmp_path: Path):
    (tmp_path / "short.csv").write_text("date,close\n2025-01-01,100\n2025-01-02,101\n", encoding="utf-8")
    try:
        ForecastMarketDirectionTool(tmp_path).execute("short-v1", "NVDA", "short.csv")
    except ValueError as exc:
        assert "at least 31" in str(exc)
    else:
        raise AssertionError("short history must fail")

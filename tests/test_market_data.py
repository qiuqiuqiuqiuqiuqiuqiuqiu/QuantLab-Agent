import json
from pathlib import Path

import pytest

from quant_lab_agent.tools.market_data import FetchUSMarketDataTool, MarketDataError


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def read(self): return json.dumps(self.payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_): return False


def test_fetches_validated_snapshot_from_ticker(tmp_path: Path):
    payload = {"Weekly Time Series": {
        "2025-01-03": {"1. open": "10", "2. high": "12", "3. low": "9", "4. close": "11", "5. volume": "100"},
        "2025-01-02": {"1. open": "9", "2. high": "11", "3. low": "8", "4. close": "10", "5. volume": "120"},
    }}
    tool = FetchUSMarketDataTool(tmp_path, api_key="secret", opener=lambda *_args, **_kwargs: FakeResponse(payload))
    result = json.loads(tool.execute("NVDA"))
    assert result["symbol"] == "NVDA"
    assert result["records"] == 2
    assert "secret" not in json.dumps(result)
    assert (tmp_path / result["csv_path"]).read_text().splitlines()[0] == "date,open,high,low,close,volume"
    assert json.loads((tmp_path / result["lineage_path"]).read_text())["validation"]["ohlcv_sanity"] == "passed"


def test_resolves_company_name_then_fetches(tmp_path: Path):
    responses = iter([
        {"bestMatches": [{"1. symbol": "MSFT", "2. name": "Microsoft Corporation", "4. region": "United States"}]},
        {"Weekly Time Series": {"2025-01-02": {"1. open": "9", "2. high": "11", "3. low": "8", "4. close": "10", "5. volume": "120"}}},
    ])
    tool = FetchUSMarketDataTool(tmp_path, api_key="secret", opener=lambda *_args, **_kwargs: FakeResponse(next(responses)))
    assert json.loads(tool.execute("Microsoft Corporation"))["symbol"] == "MSFT"


def test_rejects_invalid_ohlcv_without_writing_snapshot(tmp_path: Path):
    payload = {"Weekly Time Series": {"2025-01-02": {"1. open": "9", "2. high": "8", "3. low": "10", "4. close": "10", "5. volume": "1"}}}
    tool = FetchUSMarketDataTool(tmp_path, api_key="secret", opener=lambda *_args, **_kwargs: FakeResponse(payload))
    with pytest.raises(MarketDataError, match="high/low"):
        tool.execute("NVDA")
    assert not (tmp_path / ".quant-agent" / "market-data").exists()


def test_requires_a_market_data_key(tmp_path: Path):
    with pytest.raises(MarketDataError, match="ALPHAVANTAGE_API_KEY"):
        FetchUSMarketDataTool(tmp_path).execute("NVDA")

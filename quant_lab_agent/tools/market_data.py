"""Controlled US-equity market-data retrieval with local evidence snapshots.

The LLM can request a company name or ticker, but it never receives provider
credentials or an arbitrary URL capability.  The adapter resolves the symbol
through one approved provider, validates the returned OHLCV rows, and stores an
immutable-ish CSV + lineage JSON under the configured workspace.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import Tool
from .workspace import WorkspacePathMixin


class MarketDataError(ValueError):
    """A safe, user-actionable market-data failure."""


class FetchUSMarketDataTool(WorkspacePathMixin, Tool):
    name = "fetch_us_market_data"
    description = (
        "Resolve one US-listed company name or ticker through the approved Alpha Vantage "
        "provider, download a daily OHLCV snapshot, validate it, and save CSV plus lineage locally. "
        "Use before market analysis when the user has not supplied licensed price data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 80,
                      "description": "US stock ticker or company name, e.g. NVDA or NVIDIA"},
            "start_date": {"type": "string", "description": "Optional ISO date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "Optional ISO date YYYY-MM-DD"},
            "frequency": {"type": "string", "enum": ["daily", "weekly"], "default": "weekly",
                          "description": "Weekly is the default for multi-year research; daily may be limited by the provider plan."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    _ticker = re.compile(r"^[A-Za-z][A-Za-z.\-]{0,11}$")
    _aliases = {
        "英伟达": "NVDA", "英偉達": "NVDA", "nvidia": "NVDA",
        "苹果": "AAPL", "apple": "AAPL", "微软": "MSFT", "microsoft": "MSFT",
        "亚马逊": "AMZN", "amazon": "AMZN", "谷歌": "GOOGL", "google": "GOOGL",
        "特斯拉": "TSLA", "tesla": "TSLA", "meta": "META",
    }

    def __init__(self, workspace: Path, api_key: str = "", provider: str = "alpha_vantage", opener=None, artifact_store=None):
        super().__init__(workspace)
        self.api_key = api_key
        self.provider = provider
        self._opener = opener or urlopen
        self.artifact_store = artifact_store

    def execute(self, query: str, start_date: str | None = None, end_date: str | None = None, frequency: str = "weekly") -> str:
        if self.provider != "alpha_vantage":
            raise MarketDataError("Only the configured alpha_vantage adapter is currently supported")
        if not self.api_key:
            raise MarketDataError(
                "Market data is not configured. Set ALPHAVANTAGE_API_KEY in .env, then restart the API/CLI."
            )
        start, end = self._parse_dates(start_date, end_date)
        if frequency not in {"daily", "weekly"}:
            raise MarketDataError("frequency must be daily or weekly")
        symbol, match = self._resolve_symbol(query)
        function = "TIME_SERIES_DAILY" if frequency == "daily" else "TIME_SERIES_WEEKLY"
        series_key = "Time Series (Daily)" if frequency == "daily" else "Weekly Time Series"
        params = {"function": function, "symbol": symbol}
        if frequency == "daily":
            params["outputsize"] = "compact"
        payload = self._query(params)
        raw = payload.get(series_key)
        if not isinstance(raw, dict) or not raw:
            raise MarketDataError("Provider returned no daily OHLCV data for the resolved US symbol")
        rows = self._normalise_rows(raw, start, end)
        if not rows:
            range_hint = f" in {start} to {end}" if start or end else ""
            raise MarketDataError(f"The provider returned no daily rows{range_hint}; widen the date range or use licensed full-history data")
        return self._persist(query, symbol, match, rows, start, end, frequency)

    def _resolve_symbol(self, query: str) -> tuple[str, dict[str, str]]:
        cleaned = query.strip()
        if not cleaned or len(cleaned) > 80:
            raise MarketDataError("query must be a non-empty company name or ticker of at most 80 characters")
        alias = self._aliases.get(cleaned.casefold())
        if alias:
            return alias, {"symbol": alias, "name": cleaned, "match_type": "local_alias"}
        if self._ticker.fullmatch(cleaned):
            symbol = cleaned.upper()
            return symbol, {"symbol": symbol, "name": symbol, "match_type": "ticker_input"}
        search = self._query({"function": "SYMBOL_SEARCH", "keywords": cleaned})
        matches = search.get("bestMatches", [])
        if not isinstance(matches, list):
            matches = []
        us = [item for item in matches if isinstance(item, dict) and "united states" in str(item.get("4. region", "")).casefold()]
        if not us:
            raise MarketDataError(f"No unambiguous US-listed symbol found for '{cleaned}'. Try a ticker such as NVDA.")
        picked = us[0]
        symbol = str(picked.get("1. symbol", "")).upper()
        if not self._ticker.fullmatch(symbol):
            raise MarketDataError("Provider returned an invalid symbol during company-name resolution")
        return symbol, {"symbol": symbol, "name": str(picked.get("2. name", symbol)), "match_type": "provider_search"}

    def _query(self, params: dict[str, str]) -> dict[str, Any]:
        # API key stays within this adapter; it is intentionally omitted from all output/audit strings.
        url = "https://www.alphavantage.co/query?" + urlencode({**params, "apikey": self.api_key})
        try:
            request = Request(url, headers={"User-Agent": "QuantLab-Agent/0.2"})
            with self._opener(request, timeout=20) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise MarketDataError(f"Market-data provider HTTP {exc.code}; retry later") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MarketDataError("Could not reach the market-data provider; check network and retry") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MarketDataError("Market-data provider returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MarketDataError("Market-data provider returned an unexpected response")
        message = payload.get("Error Message") or payload.get("Note") or payload.get("Information")
        if message:
            safe = str(message).replace(self.api_key, "[redacted]")
            raise MarketDataError(f"Market-data provider rejected the request: {safe[:400]}")
        return payload

    @staticmethod
    def _parse_dates(start_date: str | None, end_date: str | None) -> tuple[date | None, date | None]:
        def parse(value: str | None) -> date | None:
            if value is None:
                return None
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise MarketDataError("Dates must use ISO format YYYY-MM-DD") from exc
        start, end = parse(start_date), parse(end_date)
        if start and end and start > end:
            raise MarketDataError("start_date must not be later than end_date")
        return start, end

    @staticmethod
    def _normalise_rows(raw: dict[str, Any], start: date | None, end: date | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day, values in raw.items():
            try:
                day_value = date.fromisoformat(str(day))
            except ValueError as exc:
                raise MarketDataError(f"Provider returned an invalid trading date: {day!r}") from exc
            if (start and day_value < start) or (end and day_value > end):
                continue
            if not isinstance(values, dict):
                raise MarketDataError("Provider returned an invalid OHLCV row")
            try:
                row = {
                    "date": day_value.isoformat(), "open": float(values["1. open"]), "high": float(values["2. high"]),
                    "low": float(values["3. low"]), "close": float(values["4. close"]), "volume": int(float(values["5. volume"])),
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError("Provider OHLCV data has a missing or non-numeric required field") from exc
            numeric = (row["open"], row["high"], row["low"], row["close"])
            if not all(math.isfinite(value) and value > 0 for value in numeric) or row["volume"] < 0:
                raise MarketDataError("Provider OHLCV data contains invalid price or volume values")
            if row["low"] > min(row["open"], row["close"], row["high"]) or row["high"] < max(row["open"], row["close"], row["low"]):
                raise MarketDataError("Provider OHLCV data violates high/low price consistency")
            rows.append(row)
        rows.sort(key=lambda row: row["date"])
        if len({row["date"] for row in rows}) != len(rows):
            raise MarketDataError("Provider OHLCV data contains duplicate trading dates")
        return rows

    def _persist(self, query: str, symbol: str, match: dict[str, str], rows: list[dict[str, Any]], start: date | None, end: date | None, frequency: str) -> str:
        root = self.workspace / ".quant-agent" / "market-data"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_symbol = re.sub(r"[^A-Z0-9._-]", "_", symbol)
        csv_path = root / f"alpha_vantage_{safe_symbol}_{frequency}_{stamp}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(rows)
        digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        metadata = {
            "provider": "alpha_vantage", "query": query, "resolved_symbol": symbol, "resolved_match": match,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "frequency": frequency, "adjusted": False,
            "date_range": {"requested_start": start.isoformat() if start else None, "requested_end": end.isoformat() if end else None,
                           "returned_start": rows[0]["date"], "returned_end": rows[-1]["date"]},
            "records": len(rows), "csv_path": str(csv_path.relative_to(self.workspace)), "sha256": digest,
            "validation": {"required_columns": "passed", "date_parse": "passed", "duplicate_dates": "passed", "ohlcv_sanity": "passed"},
        }
        meta_path = csv_path.with_suffix(".lineage.json")
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        # Keep data lineage append-only and usable outside a single agent run.
        with (self.workspace / ".quant-agent" / "data_assets.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**metadata, "asset_type": "provider_market_data"}, ensure_ascii=False) + "\n")
        artifact_refs = []
        if self.artifact_store:
            data_ref = self.artifact_store.put_file(csv_path, artifact_type="market_data", created_by="fetch_us_market_data")
            lineage_ref = self.artifact_store.put_file(
                meta_path, artifact_type="data_lineage", created_by="fetch_us_market_data",
                parent_ids=(data_ref.artifact_id,),
            )
            artifact_refs = [data_ref.to_dict(), lineage_ref.to_dict()]
        return json.dumps({
            "status": "success", "provider": "alpha_vantage", "symbol": symbol, "company": match["name"],
            "frequency": frequency, "records": len(rows), "returned_date_range": [rows[0]["date"], rows[-1]["date"]],
            "csv_path": metadata["csv_path"], "lineage_path": str(meta_path.relative_to(self.workspace)), "sha256": digest,
            "validation": metadata["validation"],
            "artifact_refs": artifact_refs,
            "warning": "Daily data is an evidence snapshot, not investment advice. Check provider licensing and history coverage before backtesting.",
        }, ensure_ascii=False)

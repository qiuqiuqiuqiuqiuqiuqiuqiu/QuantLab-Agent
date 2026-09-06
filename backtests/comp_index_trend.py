"""Starter report for a locally uploaded Nasdaq Composite-style weekly OHLC CSV.

No network access and no external packages.  Input is discovered only inside the
controlled .quant-agent/uploads directory; output is a validated JSON artifact.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

WORKSPACE = Path(__file__).resolve().parents[1]
UPLOADS = WORKSPACE / ".quant-agent" / "uploads"
OUTPUT = WORKSPACE / "artifacts" / "comp_index_trend.json"


def load_prices() -> tuple[Path, list[tuple[str, float]]]:
    candidates = sorted(UPLOADS.glob("*_index_history_COMP.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("No uploaded *_index_history_COMP.csv file was found")
    source = candidates[0]
    rows: list[tuple[str, float]] = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            date, close = (row.get("date") or "").strip(), (row.get("close") or "").strip()
            if not date or not close:
                continue
            datetime.strptime(date, "%Y-%m-%d")
            rows.append((date, float(close)))
    rows.sort(key=lambda item: item[0])
    if len(rows) < 82:
        raise ValueError("At least 82 weekly observations are required for the 10/30-week trend report")
    return source, rows[-156:]  # latest three years of weekly observations


def max_drawdown(equity: list[float]) -> float:
    peak, worst = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def main() -> None:
    source, rows = load_prices()
    dates, closes = zip(*rows)
    equity = [1.0]
    returns: list[float] = []
    turnover = 0.0
    position = 0
    for index in range(30, len(closes) - 1):
        short = mean(closes[index - 9:index + 1])
        long = mean(closes[index - 29:index + 1])
        next_position = int(short > long)
        cost = 0.0005 if next_position != position else 0.0
        daily_like_return = (closes[index + 1] / closes[index] - 1) * next_position - cost
        equity.append(equity[-1] * (1 + daily_like_return))
        returns.append(daily_like_return)
        turnover += abs(next_position - position)
        position = next_position
    volatility = pstdev(returns) * math.sqrt(52) if len(returns) > 1 else 0.0
    annual_return = equity[-1] ** (52 / len(returns)) - 1 if returns else 0.0
    sharpe = annual_return / volatility if volatility else 0.0
    start = 30
    payload = {
        "metrics": {
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_drawdown(equity), 4),
            "turnover": round(turnover / (len(returns) / 52), 4),
            "annualized_return": round(annual_return, 4),
            "annualized_volatility": round(volatility, 4),
            "observations": len(returns),
        },
        "equity_curve": [[dates[start + i], round(value, 6)] for i, value in enumerate(equity)],
        "drawdown_curve": [],
        "metadata": {
            "source": str(source.relative_to(WORKSPACE)),
            "frequency": "weekly",
            "window": "latest 156 observations / about 3 years",
            "strategy": "long/cash 10/30-week moving-average trend signal with 5 bps switching cost",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

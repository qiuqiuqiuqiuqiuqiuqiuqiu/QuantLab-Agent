"""Trusted local demo-template executor used by the QuantLab web onboarding flow.

It produces deterministic *synthetic* weekly market series when users have not
connected a market-data vendor. The synthetic origin is retained in every job
and report, so it can never be confused with a live market-data backtest.
"""
from __future__ import annotations
import hashlib, json, math, os, random
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev

job_path = Path(os.environ["QUANTLAB_JOB_FILE"])
result_path = Path(os.environ["QUANTLAB_RESULT_FILE"])
job = json.loads(job_path.read_text(encoding="utf-8"))
tickers = [ticker.upper() for ticker in job["tickers"]]
weeks = int(job.get("weeks", 156))


def series(ticker: str) -> list[tuple[str, float]]:
    seed = int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    # Stable parameters create different, plausible-looking demo profiles.
    drift = 0.0010 + (seed % 9) * 0.00016
    volatility = 0.025 + ((seed >> 4) % 10) * 0.002
    start = date.today() - timedelta(days=7 * (weeks - 1))
    price, values = 100.0, []
    for index in range(weeks):
        cycle = math.sin(index / 12 + (seed % 19)) * 0.007
        shock = rng.gauss(0, volatility)
        price *= max(0.7, 1 + drift + cycle + shock)
        values.append(((start + timedelta(days=index * 7)).isoformat(), round(price, 4)))
    return values

all_series = {ticker: series(ticker) for ticker in tickers}
returns = {ticker: [values[i][1] / values[i-1][1] - 1 for i in range(1, len(values))] for ticker, values in all_series.items()}


def stats(values: list[float]) -> dict[str, float]:
    equity = [1.0]
    for value in values: equity.append(equity[-1] * (1 + value))
    peak, drawdown = equity[0], 0.0
    for value in equity:
        peak = max(peak, value); drawdown = max(drawdown, 1 - value / peak)
    annual_return = equity[-1] ** (52 / len(values)) - 1
    annual_vol = pstdev(values) * math.sqrt(52)
    return {"annualized_return": annual_return, "annualized_volatility": annual_vol,
            "sharpe": annual_return / annual_vol if annual_vol else 0.0,
            "max_drawdown": drawdown, "turnover": 0.0}

primary = returns[tickers[0]]
turnover = 0
if job["template_id"] == "strategy":
    # A transparent long/cash 10/30-week trend overlay on the selected proxy.
    closes = [value for _, value in all_series[tickers[0]]]
    strategy, position, turnover = [], 0, 0
    for index in range(1, len(closes)):
        if index < 30:
            target = 0
        else:
            target = int(mean(closes[index-9:index+1]) > mean(closes[index-29:index+1]))
        cost = 0.0005 if target != position else 0.0
        strategy.append(primary[index-1] * target - cost)
        turnover += abs(target-position); position = target
    primary = strategy
metrics = stats(primary)
metrics["turnover"] = round((turnover / (len(primary) / 52)) if job["template_id"] == "strategy" else 0.0, 4)
equity = [1.0]
for value in primary: equity.append(round(equity[-1] * (1 + value), 6))
comparison = {ticker: {key: round(value, 4) for key, value in stats(values).items()} for ticker, values in returns.items()}
payload = {"metrics": {key: round(value, 4) for key, value in metrics.items()},
 "equity_curve": [[all_series[tickers[0]][min(i, weeks-1)][0], value] for i, value in enumerate(equity)],
 "drawdown_curve": [], "report": {"template_id": job["template_id"], "tickers": tickers, "comparison": comparison,
 "data_origin": "Synthetic demonstration data generated locally — replace with uploaded/vendor data before drawing market conclusions.",
 "method": job["method"]}}
result_path.parent.mkdir(parents=True, exist_ok=True)
result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

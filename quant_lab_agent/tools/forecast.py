"""A deliberately small, auditable statistical scenario forecaster.

This is not a trading signal or a claim of future certainty.  It transforms a
validated local price CSV into a reproducible AR(1) return scenario and reports
rolling out-of-sample diagnostics alongside every prediction.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .base import Tool
from .workspace import WorkspacePathMixin


class ForecastMarketDirectionTool(WorkspacePathMixin, Tool):
    name = "forecast_market_direction"
    description = (
        "Create an auditable statistical market-direction scenario from a validated local price CSV. "
        "Returns probability and an uncertainty interval with rolling out-of-sample diagnostics; it is not investment advice."
    )
    parameters = {
        "type": "object",
        "properties": {
            "forecast_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{2,63}$"},
            "ticker": {"type": "string", "pattern": "^[A-Za-z][A-Za-z.\\-]{0,11}$"},
            "price_data_path": {"type": "string", "description": "Workspace-relative CSV with date and close columns"},
            "horizon_bars": {"type": "integer", "minimum": 1, "maximum": 26, "default": 1},
            "frequency": {"type": "string", "enum": ["daily", "weekly"], "default": "weekly"},
        },
        "required": ["forecast_id", "ticker", "price_data_path"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path, artifact_store=None):
        super().__init__(workspace)
        self.artifact_store = artifact_store

    def execute(self, forecast_id: str, ticker: str, price_data_path: str, horizon_bars: int = 1, frequency: str = "weekly") -> str:
        if not self._valid_id(forecast_id):
            raise ValueError("forecast_id must be lowercase letters, digits, underscores or hyphens (3-64 chars)")
        if not ticker or len(ticker) > 12:
            raise ValueError("ticker is invalid")
        if not 1 <= int(horizon_bars) <= 26 or frequency not in {"daily", "weekly"}:
            raise ValueError("horizon_bars must be 1-26 and frequency must be daily or weekly")
        source = self.resolve_path(price_data_path)
        dates, closes = self._load_prices(source)
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(returns) < 30:
            raise ValueError("Forecast needs at least 31 valid closing-price rows")
        intercept, beta, residual_sigma = self._fit_ar1(returns)
        last_return = returns[-1]
        one_bar_mean = intercept + beta * last_return
        horizon_mean = sum(intercept + (beta ** step) * (last_return - (intercept / max(1e-12, 1 - beta))) for step in range(1, horizon_bars + 1)) if abs(beta) < .995 else one_bar_mean * horizon_bars
        horizon_sigma = residual_sigma * math.sqrt(horizon_bars)
        probability_up = self._normal_cdf(horizon_mean / horizon_sigma) if horizon_sigma > 0 else (1.0 if horizon_mean > 0 else 0.5)
        diagnostics = self._rolling_diagnostics(returns)
        current_price = closes[-1]
        lower = current_price * math.exp(horizon_mean - 1.96 * horizon_sigma)
        upper = current_price * math.exp(horizon_mean + 1.96 * horizon_sigma)
        confidence = self._confidence(len(returns), diagnostics)
        result = {
            "schema_version": 1,
            "forecast_id": forecast_id,
            "ticker": ticker.upper(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": {"name": "ar1_log_return_scenario", "intercept": intercept, "lag_coefficient": beta, "residual_sigma": residual_sigma},
            "data_lineage": {"price_data_path": str(source.relative_to(self.workspace)), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "observations": len(closes), "start": dates[0], "end": dates[-1], "frequency": frequency},
            "forecast": {"horizon_bars": int(horizon_bars), "current_close": current_price, "expected_log_return": horizon_mean, "expected_return": math.exp(horizon_mean) - 1, "probability_up": probability_up, "interval_95": [lower, upper], "confidence": confidence},
            "rolling_out_of_sample": diagnostics,
            "limitations": [
                "Statistical scenario only; historical patterns may not persist.",
                "Does not include fundamentals, news, macro variables, options data, corporate actions, liquidity or market impact.",
                "A probability is not a trading recommendation or a guarantee of future direction.",
            ],
        }
        artifact_dir = self.workspace / "artifacts"; artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifact_dir / f"{forecast_id}_forecast.json"
        artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact_ref = self.artifact_store.put_file(
            artifact, artifact_type="forecast_result", created_by="forecast_market_direction",
        ).to_dict() if self.artifact_store else None
        return json.dumps({
            "status": "success", "forecast_id": forecast_id, "ticker": ticker.upper(),
            "artifact_path": str(artifact.relative_to(self.workspace)), "artifact_sha256": artifact_hash,
            "artifact": artifact_ref,
            "forecast": result["forecast"], "rolling_out_of_sample": diagnostics,
            "limitations": result["limitations"],
            "next_action": "Explain this as an uncertain statistical scenario. Do not make a buy/sell recommendation or mark research as deployment-approved.",
        }, ensure_ascii=False)

    @staticmethod
    def _valid_id(value: str) -> bool:
        import re
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", value))

    @staticmethod
    def _load_prices(source: Path) -> tuple[list[str], list[float]]:
        if source.suffix.lower() != ".csv" or not source.is_file():
            raise ValueError("price_data_path must be an existing workspace-local CSV")
        rows: list[tuple[str, float]] = []
        with source.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"date", "close"}.issubset(set(reader.fieldnames or [])):
                raise ValueError("price CSV must include date and close columns")
            for row in reader:
                try:
                    close = float(row["close"])
                    if not math.isfinite(close) or close <= 0:
                        raise ValueError
                    # ISO dates sort chronologically and reject malformed entries early.
                    datetime.fromisoformat(row["date"])
                    rows.append((row["date"], close))
                except (TypeError, ValueError) as exc:
                    raise ValueError("price CSV contains invalid date or positive close values") from exc
        rows.sort(key=lambda item: item[0])
        if len({item[0] for item in rows}) != len(rows):
            raise ValueError("price CSV contains duplicate dates")
        return [item[0] for item in rows], [item[1] for item in rows]

    @staticmethod
    def _fit_ar1(values: list[float]) -> tuple[float, float, float]:
        x, y = values[:-1], values[1:]
        xbar, ybar = sum(x) / len(x), sum(y) / len(y)
        denominator = sum((value - xbar) ** 2 for value in x)
        beta = sum((a - xbar) * (b - ybar) for a, b in zip(x, y)) / denominator if denominator > 1e-16 else 0.0
        beta = max(-0.99, min(0.99, beta))
        intercept = ybar - beta * xbar
        residuals = [actual - (intercept + beta * prior) for prior, actual in zip(x, y)]
        sigma = math.sqrt(sum(value * value for value in residuals) / max(1, len(residuals) - 2))
        return intercept, beta, max(sigma, 1e-12)

    @classmethod
    def _rolling_diagnostics(cls, returns: list[float]) -> dict:
        window = min(52, max(20, len(returns) // 3))
        forecasts: list[tuple[float, int]] = []
        for index in range(window + 1, len(returns)):
            intercept, beta, sigma = cls._fit_ar1(returns[index - window:index])
            score = intercept + beta * returns[index - 1]
            probability = cls._normal_cdf(score / sigma) if sigma > 0 else 0.5
            actual = 1 if returns[index] > 0 else 0
            forecasts.append((probability, actual))
        if not forecasts:
            return {"window_bars": window, "test_observations": 0, "direction_accuracy": None, "brier_score": None, "quality": "insufficient"}
        accuracy = sum((prob >= 0.5) == bool(actual) for prob, actual in forecasts) / len(forecasts)
        brier = sum((prob - actual) ** 2 for prob, actual in forecasts) / len(forecasts)
        quality = "limited" if len(forecasts) < 20 else ("better_than_naive" if accuracy >= .53 and brier < .25 else "weak")
        return {"window_bars": window, "test_observations": len(forecasts), "direction_accuracy": accuracy, "brier_score": brier, "quality": quality}

    @staticmethod
    def _normal_cdf(value: float) -> float:
        return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))

    @staticmethod
    def _confidence(observations: int, diagnostics: dict) -> str:
        if observations < 80 or diagnostics["quality"] in {"weak", "insufficient"}:
            return "low"
        return "medium" if diagnostics["test_observations"] >= 30 else "low"

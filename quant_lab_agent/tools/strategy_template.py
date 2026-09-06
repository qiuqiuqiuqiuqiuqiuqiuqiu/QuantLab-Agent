"""Compile a small, declarative strategy specification into a deterministic runner.

This intentionally is *not* an arbitrary-code generator.  The LLM chooses only
from reviewed strategy families and numeric parameters.  The compiler writes a
human-readable spec alongside a standard-library-only Python script, so the
result can be inspected, executed by ``run_backtest``, and reproduced later.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .base import Tool
from .workspace import WorkspacePathMixin


class CreateStrategyTemplateTool(WorkspacePathMixin, Tool):
    name = "create_strategy_template"
    description = (
        "Compile a reviewed declarative strategy template into a workspace-local Python backtest script. "
        "Allowed strategies: buy_and_hold, moving_average, mean_reversion. Use only after a validated OHLCV CSV exists."
    )
    parameters = {
        "type": "object",
        "properties": {
            "research_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{2,63}$"},
            "ticker": {"type": "string", "pattern": "^[A-Za-z][A-Za-z.\\-]{0,11}$"},
            "price_data_path": {"type": "string", "description": "Workspace-relative validated OHLCV CSV path"},
            "strategy_type": {"type": "string", "enum": ["buy_and_hold", "moving_average", "mean_reversion"]},
            "frequency": {"type": "string", "enum": ["daily", "weekly"], "default": "weekly"},
            "transaction_cost_bps": {"type": "number", "minimum": 0, "maximum": 100},
            "fast_window": {"type": "integer", "minimum": 2, "maximum": 260},
            "slow_window": {"type": "integer", "minimum": 3, "maximum": 520},
            "lookback_window": {"type": "integer", "minimum": 3, "maximum": 260},
            "entry_z": {"type": "number", "minimum": -5, "maximum": -0.05},
            "exit_z": {"type": "number", "minimum": -2, "maximum": 3},
        },
        "required": ["research_id", "ticker", "price_data_path", "strategy_type"],
        "additionalProperties": False,
    }
    _research_id = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
    _ticker = re.compile(r"^[A-Za-z][A-Za-z.\-]{0,11}$")

    def execute(
        self, research_id: str, ticker: str, price_data_path: str, strategy_type: str,
        frequency: str = "weekly", transaction_cost_bps: float = 5.0,
        fast_window: int = 10, slow_window: int = 30, lookback_window: int = 20,
        entry_z: float = -1.0, exit_z: float = 0.0,
    ) -> str:
        if not self._research_id.fullmatch(research_id):
            raise ValueError("research_id must be lowercase letters, digits, underscores or hyphens (3-64 chars)")
        if not self._ticker.fullmatch(ticker):
            raise ValueError("ticker must be a valid ticker-like value")
        if strategy_type not in {"buy_and_hold", "moving_average", "mean_reversion"}:
            raise ValueError("strategy_type is not an approved template")
        if frequency not in {"daily", "weekly"}:
            raise ValueError("frequency must be daily or weekly")
        if not 0 <= float(transaction_cost_bps) <= 100:
            raise ValueError("transaction_cost_bps must be between 0 and 100")
        if not (2 <= int(fast_window) < int(slow_window) <= 520):
            raise ValueError("moving_average requires 2 <= fast_window < slow_window <= 520")
        if not 3 <= int(lookback_window) <= 260:
            raise ValueError("lookback_window must be between 3 and 260")
        if not -5 <= float(entry_z) <= -0.05 or not -2 <= float(exit_z) <= 3 or float(entry_z) >= float(exit_z):
            raise ValueError("mean_reversion requires entry_z < exit_z, with supported bounds")
        input_path = self.resolve_path(price_data_path)
        self._validate_input_csv(input_path)

        spec = {
            "schema_version": 1, "research_id": research_id, "ticker": ticker.upper(),
            "price_data_path": str(input_path.relative_to(self.workspace)), "strategy_type": strategy_type,
            "frequency": frequency, "bars_per_year": 252 if frequency == "daily" else 52,
            "transaction_cost_bps": float(transaction_cost_bps),
            "parameters": {
                "fast_window": int(fast_window), "slow_window": int(slow_window),
                "lookback_window": int(lookback_window), "entry_z": float(entry_z), "exit_z": float(exit_z),
            },
            "execution_model": "Signal from bar t is applied to return from t to t+1; no same-bar look-ahead.",
            "limitations": [
                "Close-to-close simulation only; it does not model intrabar fills.",
                "Provider adjustment, delistings, liquidity, taxes and market impact need separate review.",
            ],
        }
        spec_dir, script_dir, artifact_dir = (
            self.workspace / ".quant-agent" / "strategy-specs",
            self.workspace / "backtests" / "generated",
            self.workspace / "artifacts",
        )
        for directory in (spec_dir, script_dir, artifact_dir):
            directory.mkdir(parents=True, exist_ok=True)
        spec_path = spec_dir / f"{research_id}.json"
        script_path = script_dir / f"{research_id}.py"
        result_path = artifact_dir / f"{research_id}_strategy.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        script_path.write_text(self._render_runner(spec), encoding="utf-8")
        return json.dumps({
            "status": "success", "strategy_type": strategy_type, "ticker": ticker.upper(),
            "strategy_spec_path": str(spec_path.relative_to(self.workspace)),
            "script_path": str(script_path.relative_to(self.workspace)),
            "result_path": str(result_path.relative_to(self.workspace)),
            "rule_summary": self._rule_summary(spec),
            "next_action": "Call run_backtest with script_path and result_path. Then record_backtest and validate_research using the returned metrics.",
        }, ensure_ascii=False)

    @staticmethod
    def _validate_input_csv(path: Path) -> None:
        if path.suffix.lower() != ".csv" or not path.is_file():
            raise ValueError("price_data_path must be an existing workspace-local CSV")
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            if not {"date", "close"}.issubset(fields):
                raise ValueError("price CSV must include date and close columns")
            sample = list(reader)
        if len(sample) < 8:
            raise ValueError("price CSV needs at least 8 rows before a strategy can be compiled")

    @staticmethod
    def _rule_summary(spec: dict) -> str:
        p, kind = spec["parameters"], spec["strategy_type"]
        if kind == "buy_and_hold":
            return "Hold the asset after the first eligible bar."
        if kind == "moving_average":
            return f"Hold when close is above its {p['fast_window']}-bar average and that average is above the {p['slow_window']}-bar average; otherwise hold cash."
        return f"Buy when close is {p['entry_z']} standard deviations below its {p['lookback_window']}-bar mean; exit when z-score reaches {p['exit_z']}."

    @staticmethod
    def _render_runner(spec: dict) -> str:
        """Static standard-library runner; only the JSON spec changes per generated file."""
        encoded = json.dumps(spec, ensure_ascii=False)
        return f'''# Generated by QuantLab's reviewed strategy-template compiler. Do not edit rules silently.
import csv, json, math
from pathlib import Path

SPEC = json.loads({encoded!r})
ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / SPEC["price_data_path"]
OUTPUT = ROOT / "artifacts" / (SPEC["research_id"] + "_strategy.json")

def mean(values): return sum(values) / len(values)
def stdev(values):
    if len(values) < 2: return 0.0
    m = mean(values); return math.sqrt(sum((x-m)**2 for x in values) / (len(values)-1))
def signal(closes, index, position):
    kind, p = SPEC["strategy_type"], SPEC["parameters"]
    if kind == "buy_and_hold": return 1.0
    if kind == "moving_average":
        if index + 1 < p["slow_window"]: return 0.0
        fast = mean(closes[index-p["fast_window"]+1:index+1]); slow = mean(closes[index-p["slow_window"]+1:index+1])
        return 1.0 if closes[index] > fast and fast > slow else 0.0
    if index + 1 < p["lookback_window"]: return 0.0
    sample = closes[index-p["lookback_window"]+1:index+1]; sigma = stdev(sample)
    z = 0.0 if sigma == 0 else (closes[index] - mean(sample)) / sigma
    if position == 0.0 and z <= p["entry_z"]: return 1.0
    if position == 1.0 and z >= p["exit_z"]: return 0.0
    return position

rows = []
with INPUT.open(encoding="utf-8", newline="") as handle:
    for row in csv.DictReader(handle): rows.append((row["date"], float(row["close"])))
rows.sort(key=lambda item: item[0])
if len(rows) < 3: raise ValueError("Need at least three price rows")
dates, closes = [x[0] for x in rows], [x[1] for x in rows]
equity, peak, position, turnover = 1.0, 1.0, 0.0, 0.0
curve, drawdowns, returns = [[dates[0], equity]], [[dates[0], 0.0]], []
cost = SPEC["transaction_cost_bps"] / 10000.0
for i in range(1, len(closes)):
    target = signal(closes, i-1, position)  # information ends at t-1
    trade = abs(target-position); turnover += trade
    gross = closes[i] / closes[i-1] - 1.0
    net = target * gross - trade * cost
    equity *= 1.0 + net; returns.append(net); position = target
    peak = max(peak, equity); curve.append([dates[i], equity]); drawdowns.append([dates[i], equity / peak - 1.0])
bars = max(1, len(returns)); years = bars / SPEC["bars_per_year"]
annual_return = equity ** (1.0 / years) - 1.0 if years > 0 else 0.0
vol = stdev(returns) * math.sqrt(SPEC["bars_per_year"])
sharpe = (mean(returns) / stdev(returns) * math.sqrt(SPEC["bars_per_year"])) if stdev(returns) > 0 else 0.0
result = {{"metrics": {{"sharpe": sharpe, "max_drawdown": abs(min(x[1] for x in drawdowns)), "turnover": turnover / max(years, 1/ SPEC["bars_per_year"]), "annualized_return": annual_return, "annualized_volatility": vol, "total_return": equity-1.0}}, "equity_curve": curve, "drawdown_curve": drawdowns, "strategy_spec": SPEC}}
OUTPUT.parent.mkdir(parents=True, exist_ok=True); OUTPUT.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
'''

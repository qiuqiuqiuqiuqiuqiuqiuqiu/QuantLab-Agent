"""Friendly dependency-free CLI for local QuantLab research operations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agent import Agent
from .config import Config
from .llm import LLM
from .runtime import RunStore, archive_runs, rotate_audit_log
from .service import terminal_status
from .tools import default_tools


class UI:
    """Tiny ANSI-aware presentation layer; no Rich/Typer dependency required."""
    def __init__(self, enabled: bool | None = None):
        self.enabled = sys.stdout.isatty() if enabled is None else enabled

    def _paint(self, code: str, value: str) -> str:
        return f"\033[{code}m{value}\033[0m" if self.enabled else value

    def title(self, text: str) -> None: print(self._paint("1;32", f"\n◆ {text}"))
    def ok(self, text: str) -> None: print(self._paint("32", f"✓ {text}"))
    def warn(self, text: str) -> None: print(self._paint("33", f"! {text}"))
    def error(self, text: str) -> None: print(self._paint("31", f"✗ {text}"))
    def key(self, name: str, value: Any) -> None: print(f"  {self._paint('2', name + ':')} {value}")


def _build_agent(config: Config) -> Agent:
    if not config.api_key:
        raise ValueError("Missing model key. Add OPENROUTER_API_KEY to .env or export it in your terminal.")
    return Agent(
        LLM(config.model, config.api_key, config.base_url, max_tokens=config.max_tokens, max_retries=config.llm_max_retries),
        default_tools(config.workspace, config.market_data_api_key, config.market_data_provider), config.workspace, config.audit_log,
        config.max_context_tokens, config.max_rounds, config.max_backtest_retries, config.max_total_tokens,
        config.max_tokens, config.input_cost_per_million, config.output_cost_per_million,
    )


def _tools(config: Config) -> dict[str, Any]:
    return {tool.name: tool for tool in default_tools(config.workspace, config.market_data_api_key, config.market_data_provider)}


def _emit(payload: Any, ui: UI, *, as_json: bool = False, title: str | None = None) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2)); return
    if title: ui.title(title)
    if isinstance(payload, dict):
        status = payload.get("status")
        if status == "success": ui.ok("Completed")
        elif status == "failed": ui.error(payload.get("message", "Failed"))
        for key, value in payload.items():
            if key in {"status", "message", "stdout", "stderr"} or value in (None, "", [], {}): continue
            ui.key(key.replace("_", " "), value if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False))
    else:
        print(payload)


def _run(config: Config, prompt: str, ui: UI, resume_of: str | None = None) -> int:
    store = RunStore(config.workspace)
    run_id = store.create(prompt)
    if resume_of: store.record_event(run_id, "resumed_from", {"run_id": resume_of})
    ui.title("QuantLab research run")
    ui.key("run id", run_id); ui.key("task", prompt)
    try:
        answer = _build_agent(config).chat(prompt, run_store=store, run_id=run_id)
        status = terminal_status(answer); store.finish(run_id, answer, status)
    except Exception as exc:
        store.finish(run_id, f"Agent failed: {exc}", "failed")
        ui.error(str(exc)); return 1
    (ui.ok if status == "completed" else ui.warn)(f"Research finished with status: {status}")
    print("\n" + answer)
    ui.key("inspect", f"quantlab inspect {run_id}")
    return 0


def _doctor(config: Config, ui: UI, as_json: bool) -> int:
    report = {
        "workspace": str(config.workspace), "model": config.model,
        "model_provider_configured": bool(config.api_key),
        "market_data_provider": config.market_data_provider,
        "market_data_configured": bool(config.market_data_api_key),
        "audit_log": str(config.audit_log), "database": str(config.database_path),
        "next_step": "quantlab run -p \"Analyze NVDA over three years\"" if config.api_key else "Add OPENROUTER_API_KEY to .env, then run quantlab doctor again.",
    }
    _emit(report, ui, as_json=as_json, title="QuantLab setup check")
    if not as_json and not config.market_data_api_key:
        ui.warn("ALPHAVANTAGE_API_KEY is not set; the Agent can use uploaded CSVs but cannot fetch market data.")
    return 0 if config.api_key else 2


def _data_fetch(config: Config, args, ui: UI) -> int:
    try:
        result = json.loads(_tools(config)["fetch_us_market_data"].execute(args.query, args.start, args.end, args.frequency))
    except Exception as exc:
        ui.error(str(exc)); return 1
    _emit(result, ui, as_json=args.json, title="Market-data snapshot")
    if not args.json: ui.key("next", f"Use the csv path above in `quantlab strategy create` or `quantlab forecast`.")
    return 0


def _strategy_create(config: Config, args, ui: UI) -> int:
    try:
        created = json.loads(_tools(config)["create_strategy_template"].execute(
            args.id, args.ticker, args.data, args.type, args.frequency, args.cost,
            args.fast, args.slow, args.lookback, args.entry_z, args.exit_z,
        ))
        if args.run:
            created["backtest"] = json.loads(_tools(config)["run_backtest"].execute(created["script_path"], created["result_path"], args.timeout))
    except Exception as exc:
        ui.error(str(exc)); return 1
    _emit(created, ui, as_json=args.json, title="Strategy template")
    return 0 if created.get("backtest", {}).get("status", "success") == "success" else 1


def _forecast(config: Config, args, ui: UI) -> int:
    try:
        result = json.loads(_tools(config)["forecast_market_direction"].execute(args.id, args.ticker, args.data, args.horizon, args.frequency))
    except Exception as exc:
        ui.error(str(exc)); return 1
    _emit(result, ui, as_json=args.json, title="Statistical forecast scenario")
    if not args.json: ui.warn("This is an uncertain statistical scenario, not a buy/sell recommendation.")
    return 0


def _add_common_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of formatted terminal output")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="quantlab", description="QuantLab — local, evidence-first US-equity research agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  quantlab doctor
  quantlab data fetch NVIDIA --frequency weekly
  quantlab run -p "Analyze NVIDIA over three years; fetch real data first."
  quantlab strategy create --id nvda-ma-v1 --ticker NVDA --data .quant-agent/market-data/file.csv --type moving_average --fast 10 --slow 30 --run
  quantlab forecast --id nvda-2w-v1 --ticker NVDA --data .quant-agent/market-data/file.csv --horizon 2
""",
    )
    parser.add_argument("-p", "--prompt", help="Compatibility shorthand for `run --prompt`")
    parser.add_argument("--no-color", action="store_true", help="Disable terminal colours")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Start an Agent-led research task"); run.add_argument("-p", "--prompt", required=True)
    inspect = sub.add_parser("inspect", help="Show a saved run manifest"); inspect.add_argument("run_id"); _add_common_output(inspect)
    history = sub.add_parser("history", help="List recent research runs"); history.add_argument("--limit", type=int, default=20); _add_common_output(history)
    resume = sub.add_parser("resume", help="Re-run a saved task from its original prompt"); resume.add_argument("run_id")
    doctor = sub.add_parser("doctor", help="Check keys, workspace and local paths"); _add_common_output(doctor)

    data = sub.add_parser("data", help="Fetch and inspect local research data")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    fetch = data_sub.add_parser("fetch", help="Resolve a company/ticker and save a validated market-data snapshot")
    fetch.add_argument("query", help="Company name or ticker, e.g. NVIDIA or NVDA"); fetch.add_argument("--start"); fetch.add_argument("--end")
    fetch.add_argument("--frequency", choices=("daily", "weekly"), default="weekly"); _add_common_output(fetch)

    strategy = sub.add_parser("strategy", help="Create a reviewed strategy template")
    strategy_sub = strategy.add_subparsers(dest="strategy_command", required=True)
    create = strategy_sub.add_parser("create", help="Compile a strategy spec; use --run to execute it immediately")
    create.add_argument("--id", required=True, help="lowercase research id, e.g. nvda-ma-v1"); create.add_argument("--ticker", required=True)
    create.add_argument("--data", required=True, help="workspace-relative CSV with date and close columns")
    create.add_argument("--type", choices=("buy_and_hold", "moving_average", "mean_reversion"), required=True)
    create.add_argument("--frequency", choices=("daily", "weekly"), default="weekly"); create.add_argument("--cost", type=float, default=5.0, help="one-way transaction cost in bps")
    create.add_argument("--fast", type=int, default=10); create.add_argument("--slow", type=int, default=30); create.add_argument("--lookback", type=int, default=20)
    create.add_argument("--entry-z", type=float, default=-1.0); create.add_argument("--exit-z", type=float, default=0.0); create.add_argument("--run", action="store_true")
    create.add_argument("--timeout", type=int, default=120); _add_common_output(create)

    forecast = sub.add_parser("forecast", help="Create a statistical probability scenario from a local price CSV")
    forecast.add_argument("--id", required=True); forecast.add_argument("--ticker", required=True); forecast.add_argument("--data", required=True)
    forecast.add_argument("--horizon", type=int, default=1, help="forecast horizon in bars, 1-26")
    forecast.add_argument("--frequency", choices=("daily", "weekly"), default="weekly"); _add_common_output(forecast)

    cleanup = sub.add_parser("cleanup", help="Preview or archive old terminal run folders"); cleanup.add_argument("--older-than", type=int, default=90); cleanup.add_argument("--execute", action="store_true")
    logs = sub.add_parser("logs", help="Audit-log maintenance"); logs_sub = logs.add_subparsers(dest="logs_command", required=True)
    rotate = logs_sub.add_parser("rotate", help="Preview or rotate the append-only audit JSONL"); rotate.add_argument("--max-mb", type=float, default=20.0); rotate.add_argument("--execute", action="store_true")

    # argparse global options normally must come before subcommands.  Accept
    # --no-color in either position because terminal users naturally type
    # `quantlab doctor --no-color`.
    argv = list(sys.argv[1:])
    no_color = "--no-color" in argv
    if no_color:
        argv.remove("--no-color")
    args = parser.parse_args(argv); config = Config.from_env(load_dotenv=True); ui = UI(enabled=False if no_color else None)
    if args.prompt and not args.command: return _run(config, args.prompt, ui)
    store = RunStore(config.workspace)
    if args.command == "run": return _run(config, args.prompt, ui)
    if args.command == "doctor": return _doctor(config, ui, args.json)
    if args.command == "data" and args.data_command == "fetch": return _data_fetch(config, args, ui)
    if args.command == "strategy" and args.strategy_command == "create": return _strategy_create(config, args, ui)
    if args.command == "forecast": return _forecast(config, args, ui)
    if args.command == "inspect": _emit(store.load(args.run_id), ui, as_json=args.json, title="Research run"); return 0
    if args.command == "history":
        rows = store.list_runs()[:args.limit]
        if args.json: print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            ui.title("Recent research runs")
            if not rows: print("  No saved runs yet. Start with: quantlab doctor")
            for item in rows: print(f"  {item['run_id']}  {item['status']:<13}  {item['created_at']}  {item['prompt'][:70]}")
        return 0
    if args.command == "resume": return _run(config, store.load(args.run_id)["prompt"], ui, resume_of=args.run_id)
    if args.command == "cleanup":
        report = archive_runs(config.workspace, args.older_than, execute=args.execute); _emit(report, ui, title="Run archive")
        if not args.execute: ui.warn("Dry run only. Add --execute after reviewing candidates.")
        return 0
    if args.command == "logs" and args.logs_command == "rotate":
        report = rotate_audit_log(config.audit_log, max(1, round(args.max_mb * 1024 * 1024)), execute=args.execute); _emit(report, ui, title="Audit log rotation")
        if not args.execute: ui.warn("Dry run only. Add --execute after reviewing the plan.")
        return 0
    parser.print_help(); return 2


if __name__ == "__main__": raise SystemExit(main())

"""Dependency-free command line interface for research-agent runs."""
from __future__ import annotations

import argparse
import json
from .agent import Agent
from .config import Config
from .llm import LLM
from .runtime import RunStore
from .tools import default_tools


def _build_agent(config: Config) -> Agent:
    if not config.api_key:
        raise ValueError("Set OPENROUTER_API_KEY (recommended) or QUANT_AGENT_API_KEY / OPENAI_API_KEY")
    return Agent(
        LLM(config.model, config.api_key, config.base_url, max_tokens=config.max_tokens),
        default_tools(config.workspace), config.workspace, config.audit_log,
        config.max_context_tokens, config.max_rounds, config.max_backtest_retries,
    )


def _run(config: Config, prompt: str, resume_of: str | None = None) -> int:
    store = RunStore(config.workspace)
    run_id = store.create(prompt)
    if resume_of:
        store.record_event(run_id, "resumed_from", {"run_id": resume_of})
    try:
        answer = _build_agent(config).chat(prompt, run_store=store, run_id=run_id)
        status = "needs_review" if answer.startswith("[Stopped:") else "completed"
        store.finish(run_id, answer, status)
    except Exception as exc:
        store.finish(run_id, f"Agent failed: {exc}", "failed")
        raise
    print(f"Run: {run_id}\n\n{answer}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantLab: a lightweight, auditable quantitative research agent")
    parser.add_argument("-p", "--prompt", help="Compatibility shorthand for `run --prompt`")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Start a research task")
    run.add_argument("-p", "--prompt", required=True, help="Natural-language research task")

    inspect = sub.add_parser("inspect", help="Show a persisted run manifest")
    inspect.add_argument("run_id")

    history = sub.add_parser("history", help="List recent runs")
    history.add_argument("--limit", type=int, default=20)

    resume = sub.add_parser("resume", help="Re-run a previous task from its persisted prompt")
    resume.add_argument("run_id")

    args = parser.parse_args()
    config = Config.from_env()
    # Keep the original single-command experience working.
    if args.prompt and not args.command:
        return _run(config, args.prompt)
    store = RunStore(config.workspace)
    if args.command == "run":
        return _run(config, args.prompt)
    if args.command == "inspect":
        print(json.dumps(store.load(args.run_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "history":
        for item in store.list_runs()[:args.limit]:
            print(f"{item['run_id']}  {item['status']:<13}  {item['created_at']}  {item['prompt'][:72]}")
        return 0
    if args.command == "resume":
        prior = store.load(args.run_id)
        return _run(config, prior["prompt"], resume_of=args.run_id)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

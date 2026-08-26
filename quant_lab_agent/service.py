"""Application service shared by the CLI and the Web API."""
from __future__ import annotations

from .agent import Agent
from .config import Config
from .llm import LLM
from .runtime import RunStore
from .tools import default_tools


def build_agent(config: Config) -> Agent:
    if not config.api_key:
        raise RuntimeError("Set OPENROUTER_API_KEY (recommended) or QUANT_AGENT_API_KEY / OPENAI_API_KEY before starting a run")
    return Agent(
        LLM(config.model, config.api_key, config.base_url, max_tokens=config.max_tokens),
        default_tools(config.workspace), config.workspace, config.audit_log,
        config.max_context_tokens, config.max_rounds, config.max_backtest_retries,
    )


def execute_run(config: Config, run_id: str) -> str:
    """Run an already-persisted task and always record a terminal state."""
    store = RunStore(config.workspace)
    prompt = store.load(run_id)["prompt"]
    try:
        answer = build_agent(config).chat(prompt, run_store=store, run_id=run_id)
        status = "needs_review" if answer.startswith("[Stopped:") else "completed"
        store.finish(run_id, answer, status)
        return answer
    except Exception as exc:
        message = f"Agent failed: {exc}"
        store.finish(run_id, message, "failed")
        return message

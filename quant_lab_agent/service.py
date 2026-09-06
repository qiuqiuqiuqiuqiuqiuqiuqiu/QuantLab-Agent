"""Application service shared by the CLI and the Web API."""
from __future__ import annotations

from .agent import Agent
from .config import Config
from .llm import LLM
from .runtime import RunStore
from .tools import default_tools


def terminal_status(answer: str) -> str:
    """Do not label an explicit review escalation as a completed research result."""
    normalized = answer.lower()
    needs_input_markers = ("请补充", "请提供", "please provide", "need more information", "clarify")
    needs_data_markers = ("backtest could not run", "no metrics were produced", "script does not exist", "缺少分析脚本", "没有生成可验证")
    if any(marker in normalized for marker in needs_input_markers):
        return "needs_input"
    if any(marker in normalized for marker in needs_data_markers):
        return "needs_data"
    if answer.startswith("[Stopped:") or "needs_review" in normalized or "needs review" in normalized:
        return "needs_review"
    return "completed"


def build_agent(config: Config) -> Agent:
    if not config.api_key:
        raise RuntimeError("Set OPENROUTER_API_KEY (recommended) or QUANT_AGENT_API_KEY / OPENAI_API_KEY before starting a run")
    return Agent(
        LLM(config.model, config.api_key, config.base_url, max_tokens=config.max_tokens, max_retries=config.llm_max_retries),
        default_tools(config.workspace, config.market_data_api_key, config.market_data_provider), config.workspace, config.audit_log,
        config.max_context_tokens, config.max_rounds, config.max_backtest_retries, config.max_total_tokens,
        config.max_tokens, config.input_cost_per_million, config.output_cost_per_million,
    )


def execute_run(config: Config, run_id: str) -> str:
    """Run an already-persisted task and always record a terminal state."""
    store = RunStore(config.workspace)
    prompt = store.load(run_id)["prompt"]
    try:
        answer = build_agent(config).chat(prompt, run_store=store, run_id=run_id)
        status = terminal_status(answer)
        store.finish(run_id, answer, status)
        return answer
    except Exception as exc:
        message = f"Agent failed: {exc}"
        store.finish(run_id, message, "failed")
        return message

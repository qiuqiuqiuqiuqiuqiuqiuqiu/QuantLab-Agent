"""Application service shared by the CLI and the Web API."""
from __future__ import annotations

from .agent import Agent
from .config import Config
from .llm import LLM
from .runtime import AgentRegistry, AgentSpec, ArtifactStore, MultiAgentRuntime, PlanFirstCoordinator, RunStore, TaskStore
from .tools import default_tools
from .tools.backtest import RunBacktestTool
from .tools.artifacts import ReadArtifactTool
from .tools.governance import RequestApprovalTool, ValidateResearchTool
from .tools.forecast import ForecastMarketDirectionTool
from .tools.market_data import FetchUSMarketDataTool
from .tools.research import RecordBacktestTool, RegisterResearchTool
from .tools.strategy_template import CreateStrategyTemplateTool
from .tools.workspace import ReadWorkspaceFileTool, ReplaceWorkspaceTextTool


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


def _new_llm(config: Config) -> LLM:
    return LLM(config.model, config.api_key, config.base_url, max_tokens=config.max_tokens, max_retries=config.llm_max_retries)


def _specialist(config: Config, name: str, instructions: str, tools) -> Agent:
    return Agent(
        _new_llm(config), tools, config.workspace, config.audit_log,
        config.max_context_tokens, min(config.max_rounds, 10), config.max_backtest_retries,
        config.max_total_tokens, config.max_tokens, config.input_cost_per_million,
        config.output_cost_per_million, name=name,
        system_override=instructions, require_structured_final=True,
    )


def build_multi_agent(config: Config, *, run_store: RunStore, run_id: str | None = None):
    """Build a strict plan-first topology around bounded specialists."""
    registry = AgentRegistry()
    artifacts = ArtifactStore(config.workspace)
    specs = [
        AgentSpec("research_agent", "Explain quantitative concepts and inspect existing research evidence", lambda: _specialist(
            config, "research_agent",
            "You are QuantLab's research analyst. Answer bounded research questions from supplied evidence. Do not invent market data, backtest metrics, or approvals.",
            [ReadArtifactTool(artifacts), ReadWorkspaceFileTool(config.workspace)],
        ), capabilities=frozenset({"analyze_research", "explain_quant_concept"})),
        AgentSpec("data_agent", "Acquire and validate market data", lambda: _specialist(
            config, "data_agent",
            "You are QuantLab's data specialist. Acquire and validate market data, preserve lineage, never invent data or expose credentials.",
            [FetchUSMarketDataTool(config.workspace, config.market_data_api_key, config.market_data_provider, artifact_store=artifacts), ReadArtifactTool(artifacts), ReadWorkspaceFileTool(config.workspace)],
        ), capabilities=frozenset({"fetch_market_data", "validate_market_data"})),
        AgentSpec("strategy_agent", "Create reviewed declarative strategies", lambda: _specialist(
            config, "strategy_agent",
            "You are QuantLab's strategy specialist. Only create whitelisted declarative strategy templates. State assumptions, costs and limitations.",
            [CreateStrategyTemplateTool(config.workspace, artifact_store=artifacts), RegisterResearchTool(config.workspace), ReadArtifactTool(artifacts), ReadWorkspaceFileTool(config.workspace)],
        ), capabilities=frozenset({"design_strategy", "register_research"})),
        AgentSpec("backtest_agent", "Run and diagnose bounded backtests", lambda: _specialist(
            config, "backtest_agent",
            "You are QuantLab's backtest specialist. Run only workspace backtests. Treat successful validated JSON as truth. Make at most one targeted repair per failure and never install packages.",
            [RunBacktestTool(config.workspace, artifact_store=artifacts), ReadArtifactTool(artifacts), ReadWorkspaceFileTool(config.workspace), ReplaceWorkspaceTextTool(config.workspace), RecordBacktestTool(config.workspace)],
        ), capabilities=frozenset({"run_backtest", "diagnose_backtest"})),
        AgentSpec("forecast_agent", "Produce auditable statistical scenarios", lambda: _specialist(
            config, "forecast_agent",
            "You are QuantLab's forecast specialist. Produce only auditable statistical scenarios with out-of-sample diagnostics, never trading certainty or advice.",
            [ForecastMarketDirectionTool(config.workspace, artifact_store=artifacts), ReadArtifactTool(artifacts)],
        ), capabilities=frozenset({"forecast_market_direction"})),
        AgentSpec("governance_agent", "Apply deterministic research and approval gates", lambda: _specialist(
            config, "governance_agent",
            "You are QuantLab's governance specialist. Apply deterministic checks and never equate validation with deployment approval.",
            [ValidateResearchTool(config.workspace), RequestApprovalTool(config.workspace), ReadArtifactTool(artifacts)],
        ), capabilities=frozenset({"validate_research", "request_approval"})),
    ]
    for spec in specs:
        registry.register(spec)
    runtime = MultiAgentRuntime(registry, TaskStore(config.database_path), run_store, artifacts)
    def synthesizer_factory():
        return _specialist(
            config, "coordinator",
            "You are QuantLab's final synthesizer. You cannot delegate or execute tools. Summarize only the validated plan results supplied to you. Clearly report missing, failed, or review-required work. A successful backtest is never deployment approval.",
            [],
        )
    return PlanFirstCoordinator(
        _new_llm(config), synthesizer_factory, runtime, registry, run_id=run_id,
    )


def build_agent(config: Config, *, run_store: RunStore | None = None, run_id: str | None = None) -> Agent | PlanFirstCoordinator:
    if not config.api_key:
        raise RuntimeError("Set OPENROUTER_API_KEY (recommended) or QUANT_AGENT_API_KEY / OPENAI_API_KEY before starting a run")
    if config.multi_agent_enabled:
        return build_multi_agent(config, run_store=run_store or RunStore(config.workspace), run_id=run_id)
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
        agent = build_agent(config, run_store=store, run_id=run_id)
        answer = agent.chat(prompt, run_store=store, run_id=run_id)
        status = agent.last_decision.status if agent.last_decision else terminal_status(answer)
        store.finish(run_id, answer, status)
        return answer
    except Exception as exc:
        message = f"Agent failed: {exc}"
        store.finish(run_id, message, "failed")
        return message

from pathlib import Path
from quant_lab_agent import Agent, LLMResponse, ScriptedLLM, ToolCall, default_tools

def test_agent_executes_evidence_then_returns_answer(tmp_path: Path):
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("call_1", "register_research", {"research_id":"mean-reversion-v1", "hypothesis":"Short-term reversals persist.", "data_sources":["vendor://prices/v1"], "methodology":"Walk-forward backtest", "reproduce_command":"python research.py"})]),
        LLMResponse(content="Research record captured; awaiting backtest evidence and governance validation."),
    ])
    agent = Agent(llm, default_tools(tmp_path), tmp_path, tmp_path / "audit.jsonl")
    assert "awaiting" in agent.chat("Register the study")
    assert (tmp_path / ".quant-agent" / "research.json").exists()
    assert "tool_call" in (tmp_path / "audit.jsonl").read_text()

def test_validation_never_claims_deployment_approval(tmp_path: Path):
    tools = {tool.name: tool for tool in default_tools(tmp_path)}
    tools["register_research"].execute(research_id="x", hypothesis="h", data_sources=["d"], methodology="m", reproduce_command="run")
    result = tools["validate_research"].execute("x", {"sharpe": 1.2, "max_drawdown": .1, "turnover": 1})
    assert "validated_pending_approval" in result
    assert "deployment approval" in result

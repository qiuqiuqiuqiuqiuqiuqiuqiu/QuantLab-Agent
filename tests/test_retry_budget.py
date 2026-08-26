from pathlib import Path

from quant_lab_agent import Agent, LLMResponse, ScriptedLLM, ToolCall
from quant_lab_agent.tools.base import Tool


class FailingBacktestTool(Tool):
    name = "run_backtest"
    description = "test tool"
    parameters = {"type": "object", "properties": {"script_path": {"type": "string"}}, "required": ["script_path"]}

    def __init__(self):
        self.calls = 0

    def execute(self, script_path: str) -> str:
        self.calls += 1
        return '{"status":"failed", "failure_category":"runtime_error"}'


def test_backtest_retry_budget_blocks_third_attempt(tmp_path: Path):
    tool = FailingBacktestTool()
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("1", "run_backtest", {"script_path": "x.py"})]),
        LLMResponse(tool_calls=[ToolCall("2", "run_backtest", {"script_path": "x.py"})]),
        LLMResponse(tool_calls=[ToolCall("3", "run_backtest", {"script_path": "x.py"})]),
        LLMResponse(content="needs_review"),
    ])
    agent = Agent(llm, [tool], tmp_path, tmp_path / "audit.jsonl", max_backtest_retries=1)
    assert agent.chat("run it") == "needs_review"
    assert tool.calls == 2
    assert "retry budget exhausted" in (tmp_path / "audit.jsonl").read_text()

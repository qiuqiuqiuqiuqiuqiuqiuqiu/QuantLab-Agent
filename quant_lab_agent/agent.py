"""The agent kernel: model → tools → evidence → model, bounded and auditable."""
from __future__ import annotations

import inspect
import json
from time import perf_counter

from .audit import AuditLog
from .context import ContextManager
from .prompt import system_prompt


class Agent:
    def __init__(
        self, llm, tools, workspace, audit_log, max_context_tokens=64_000,
        max_rounds=30, max_backtest_retries=2,
    ):
        self.llm, self.tools, self.workspace = llm, tools, workspace
        self.messages, self.max_rounds = [], max_rounds
        self.max_backtest_retries = max_backtest_retries
        self.by_name = {tool.name: tool for tool in tools}
        self.audit, self.context = AuditLog(audit_log), ContextManager(max_context_tokens)
        self.system = system_prompt(workspace, tools)

    def chat(self, user_input: str, on_token=None, on_tool=None, run_store=None, run_id=None) -> str:
        """Execute one bounded task and persist lightweight trace events when requested."""
        self.messages.append({"role": "user", "content": user_input})
        self.audit.record("user_request", content=user_input, run_id=run_id)
        backtest_attempts: dict[str, int] = {}
        for round_number in range(1, self.max_rounds + 1):
            self.context.maybe_compress(self.messages)
            started = perf_counter()
            response = self.llm.chat(
                [{"role": "system", "content": self.system}, *self.messages],
                [t.schema() for t in self.tools], on_token,
            )
            llm_event = {
                "round": round_number,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "tool_call_count": len(response.tool_calls),
            }
            self.audit.record("llm_response", **llm_event, run_id=run_id)
            if run_store and run_id:
                run_store.record_event(run_id, "llm_response", llm_event)
            self.messages.append(response.message)
            if not response.tool_calls:
                self.audit.record("final_response", round=round_number, content=response.content, run_id=run_id)
                if run_store and run_id:
                    run_store.record_event(run_id, "final_response", {"round": round_number, "content": response.content})
                return response.content
            for call in response.tool_calls:
                if on_tool:
                    on_tool(call.name, call.arguments)
                if call.name == "run_backtest":
                    key = str(call.arguments.get("script_path", "<unknown>"))
                    backtest_attempts[key] = backtest_attempts.get(key, 0) + 1
                    if backtest_attempts[key] > 1 + self.max_backtest_retries:
                        result = (
                            "Error: backtest retry budget exhausted for " + key +
                            "; stop repairing and request human review."
                        )
                        elapsed_ms = 0.0
                    else:
                        result, elapsed_ms = self._execute_timed(call.name, call.arguments)
                        self._record_tool_event(run_store, run_id, round_number, call, result, elapsed_ms)
                        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                        continue
                else:
                    result, elapsed_ms = self._execute_timed(call.name, call.arguments)
                self._record_tool_event(run_store, run_id, round_number, call, result, elapsed_ms)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        outcome = "[Stopped: maximum tool rounds reached; human review required.]"
        self.audit.record("agent_stopped", reason="max_rounds", run_id=run_id)
        return outcome

    def _record_tool_event(self, run_store, run_id, round_number, call, result, elapsed_ms) -> None:
        event = {
            "round": round_number, "tool": call.name, "arguments": call.arguments,
            "result": result, "latency_ms": elapsed_ms,
        }
        if call.name == "run_backtest":
            try:
                payload = json.loads(result)
                if payload.get("status") == "failed":
                    event["failure_category"] = payload.get("failure_category")
            except (TypeError, json.JSONDecodeError):
                pass
        self.audit.record("tool_call", **event, run_id=run_id)
        if run_store and run_id:
            run_store.record_event(run_id, "tool_call", event)

    def _execute_timed(self, name, arguments) -> tuple[str, float]:
        started = perf_counter()
        result = self._execute(name, arguments)
        return result, round((perf_counter() - started) * 1000, 2)

    def _execute(self, name, arguments) -> str:
        tool = self.by_name.get(name)
        if not tool:
            return f"Error: unknown tool '{name}'"
        try:
            inspect.signature(tool.execute).bind(**arguments)
        except TypeError as exc:
            return f"Error: invalid arguments for {name}: {exc}"
        try:
            return tool.execute(**arguments)
        except Exception as exc:
            return f"Error executing {name}: {exc}"

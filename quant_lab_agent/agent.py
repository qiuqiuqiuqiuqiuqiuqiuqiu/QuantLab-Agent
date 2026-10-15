"""The agent kernel: model → tools → evidence → model, bounded and auditable."""
from __future__ import annotations

import inspect
import json
from time import perf_counter

from .audit import AuditLog
from .context import ContextManager, estimate_tokens
from .prompt import system_prompt
from .protocols import RunDecision


class Agent:
    def __init__(
        self, llm, tools, workspace, audit_log, max_context_tokens=64_000,
        max_rounds=30, max_backtest_retries=2, max_total_tokens=40_000,
        max_completion_tokens=4_096, input_cost_per_million=0.0, output_cost_per_million=0.0,
        name="quantlab_agent", system_override=None, require_structured_final=False,
    ):
        self.llm, self.tools, self.workspace = llm, tools, workspace
        self.messages, self.max_rounds = [], max_rounds
        self.max_backtest_retries = max_backtest_retries
        self.max_total_tokens = max_total_tokens
        self.max_completion_tokens = max_completion_tokens
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.name = name
        self.require_structured_final = require_structured_final
        self.last_decision: RunDecision | None = None
        self.by_name = {tool.name: tool for tool in tools}
        self.audit, self.context = AuditLog(audit_log), ContextManager(max_context_tokens)
        self.system = system_override or system_prompt(workspace, tools)
        if require_structured_final:
            self.system += """

Final-output contract:
Return the final response as one JSON object with keys: status, user_message,
reason_codes, evidence_artifact_ids. status must be one of completed, needs_input,
needs_data, needs_review, rejected, validated_pending_approval, failed, cancelled.
Do not wrap the JSON in Markdown.
"""

    def chat(self, user_input: str, on_token=None, on_tool=None, run_store=None, run_id=None) -> str:
        """Execute one bounded task and persist lightweight trace events when requested."""
        self.messages.append({"role": "user", "content": user_input})
        self.audit.record("user_request", content=user_input, run_id=run_id)
        backtest_attempts: dict[str, int] = {}
        tool_cache: dict[str, str] = {}
        total_prompt_tokens = total_completion_tokens = 0
        for round_number in range(1, self.max_rounds + 1):
            if self.context.maybe_compress(self.messages):
                event = {"round": round_number, "estimated_tokens": estimate_tokens(self.messages)}
                self.audit.record("context_compacted", **event, run_id=run_id)
                if run_store and run_id:
                    run_store.record_event(run_id, "context_compacted", event)
            predicted_input = estimate_tokens([{"role": "system", "content": self.system}, *self.messages])
            if total_prompt_tokens + total_completion_tokens + predicted_input + self.max_completion_tokens > self.max_total_tokens:
                outcome = "[Stopped: insufficient token budget for another model call; human review required.]"
                self._record_summary(run_store, run_id, round_number - 1, total_prompt_tokens, total_completion_tokens, "preflight_token_budget")
                self.audit.record("agent_stopped", reason="preflight_token_budget", run_id=run_id)
                return outcome
            started = perf_counter()
            response = self.llm.chat(
                [{"role": "system", "content": self.system}, *self.messages],
                [t.schema() for t in self.tools], on_token,
            )
            llm_event = {
                "agent": self.name,
                "round": round_number,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "tool_call_count": len(response.tool_calls),
            }
            total_prompt_tokens += response.prompt_tokens
            total_completion_tokens += response.completion_tokens
            llm_event["total_tokens"] = total_prompt_tokens + total_completion_tokens
            llm_event["estimated_cost_usd"] = self._estimated_cost(total_prompt_tokens, total_completion_tokens)
            self.audit.record("llm_response", **llm_event, run_id=run_id)
            if run_store and run_id:
                run_store.record_event(run_id, "llm_response", llm_event)
            self.messages.append(response.message)
            if total_prompt_tokens + total_completion_tokens > self.max_total_tokens:
                outcome = "[Stopped: token budget reached; human review required.]"
                self._record_summary(run_store, run_id, round_number, total_prompt_tokens, total_completion_tokens, "token_budget")
                self.audit.record("agent_stopped", reason="token_budget", run_id=run_id)
                return outcome
            if not response.tool_calls:
                self.last_decision = RunDecision.from_model_output(response.content)
                if self.require_structured_final and self.last_decision is None:
                    self.last_decision = RunDecision(
                        status="needs_review",
                        user_message=response.content or "Agent returned an invalid structured result; human review required.",
                        reason_codes=("invalid_structured_output",),
                    )
                final_content = self.last_decision.user_message if self.last_decision else response.content
                self.audit.record("final_response", round=round_number, agent=self.name, content=final_content,
                                  decision=self.last_decision.status if self.last_decision else None, run_id=run_id)
                if run_store and run_id:
                    run_store.record_event(run_id, "final_response", {
                        "round": round_number, "agent": self.name, "content": final_content,
                        "decision": self.last_decision.status if self.last_decision else None,
                    })
                self._record_summary(run_store, run_id, round_number, total_prompt_tokens, total_completion_tokens, "final_response")
                return final_content
            for call in response.tool_calls:
                if on_tool:
                    on_tool(call.name, call.arguments)
                fingerprint = self._tool_fingerprint(call.name, call.arguments)
                tool = self.by_name.get(call.name)
                if tool and tool.cacheable and fingerprint in tool_cache:
                    result, elapsed_ms = tool_cache[fingerprint], 0.0
                    self._record_tool_event(run_store, run_id, round_number, call, result, elapsed_ms, cache_hit=True)
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                    continue
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
                if tool and tool.cacheable and not result.startswith("Error"):
                    tool_cache[fingerprint] = result
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        outcome = "[Stopped: maximum tool rounds reached; human review required.]"
        self._record_summary(run_store, run_id, self.max_rounds, total_prompt_tokens, total_completion_tokens, "max_rounds")
        self.audit.record("agent_stopped", reason="max_rounds", run_id=run_id)
        return outcome

    def _record_summary(self, run_store, run_id, rounds, prompt_tokens, completion_tokens, outcome) -> None:
        event = {"agent": self.name, "rounds": rounds, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                 "total_tokens": prompt_tokens + completion_tokens, "estimated_cost_usd": self._estimated_cost(prompt_tokens, completion_tokens), "outcome": outcome}
        self.audit.record("agent_summary", **event, run_id=run_id)
        if run_store and run_id:
            run_store.record_event(run_id, "agent_summary", event)

    def _record_tool_event(self, run_store, run_id, round_number, call, result, elapsed_ms, cache_hit=False) -> None:
        event = {
            "agent": self.name, "round": round_number, "tool": call.name, "arguments": call.arguments,
            "result": result, "latency_ms": elapsed_ms,
        }
        if cache_hit:
            event["cache_hit"] = True
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

    @staticmethod
    def _tool_fingerprint(name, arguments) -> str:
        return name + ":" + json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _estimated_cost(self, prompt_tokens, completion_tokens) -> float:
        return round((prompt_tokens * self.input_cost_per_million + completion_tokens * self.output_cost_per_million) / 1_000_000, 8)

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

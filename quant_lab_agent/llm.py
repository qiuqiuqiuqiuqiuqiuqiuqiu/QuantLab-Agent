"""Provider boundary. The agent loop depends only on this compact interface."""
from __future__ import annotations
from dataclasses import dataclass, field
import json

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def message(self) -> dict:
        message = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in self.tool_calls]
        return message

class ScriptedLLM:
    """Offline deterministic LLM used for demos and tests."""
    def __init__(self, turns: list[LLMResponse]):
        self.turns = list(turns)
        self.total_prompt_tokens = self.total_completion_tokens = 0

    def chat(self, messages, tools=None, on_token=None) -> LLMResponse:
        if not self.turns:
            raise RuntimeError("ScriptedLLM ran out of turns")
        response = self.turns.pop(0)
        if on_token and response.content:
            on_token(response.content)
        return response

class LLM:
    """OpenAI-compatible non-streaming adapter; install with ``[openai]``."""
    def __init__(self, model: str, api_key: str, base_url: str | None = None, **kwargs):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install optional dependency: pip install '.[openai]'") from exc
        self.model, self.client, self.extra = model, OpenAI(api_key=api_key, base_url=base_url), kwargs
        self.total_prompt_tokens = self.total_completion_tokens = 0

    def chat(self, messages, tools=None, on_token=None) -> LLMResponse:
        result = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools or None, **self.extra)
        msg = result.choices[0].message
        calls = [ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in (msg.tool_calls or [])]
        usage = result.usage
        self.total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        content = msg.content or ""
        if on_token and content:
            on_token(content)
        return LLMResponse(content, calls, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)

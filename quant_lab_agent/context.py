"""Three-stage context budget management: trim, summarize, then collapse."""
def estimate_tokens(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "") + str(m.get("tool_calls") or "")) // 4 for m in messages)

class ContextManager:
    def __init__(self, max_tokens=64_000): self.max_tokens = max_tokens
    def maybe_compress(self, messages: list[dict]) -> bool:
        changed = False
        if estimate_tokens(messages) > self.max_tokens // 2:
            for message in messages:
                if message.get("role") == "tool" and len(message.get("content", "")) > 2000:
                    text = message["content"]
                    message["content"] = text[:1200] + "\n…[tool output trimmed]…\n" + text[-400:]
                    changed = True
        if estimate_tokens(messages) > self.max_tokens * 7 // 10 and len(messages) > 8:
            old, tail = messages[:-6], messages[-6:]
            facts = "\n".join(f"[{m.get('role')}] {str(m.get('content', ''))[:240]}" for m in old)
            messages[:] = [{"role": "user", "content": "[Compressed history]\n" + facts[:5000]}, {"role": "assistant", "content": "Context acknowledged."}] + tail
            changed = True
        if estimate_tokens(messages) > self.max_tokens * 9 // 10:
            messages[:] = messages[-4:]
            changed = True
        return changed

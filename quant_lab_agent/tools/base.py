from abc import ABC, abstractmethod
class Tool(ABC):
    name: str; description: str; parameters: dict
    # Only pure, read-only tools may opt into per-run memoization.
    cacheable: bool = False
    @abstractmethod
    def execute(self, **kwargs) -> str: ...
    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

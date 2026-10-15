"""QuantLab Agent — lightweight, auditable quantitative research runtime."""
from .agent import Agent
from .config import Config
from .llm import LLM, LLMResponse, ScriptedLLM, ToolCall
from .protocols import AgentResult, AgentTask, ArtifactRef, PlanTask, ResearchPlan, RunDecision
from .tools import default_tools

__version__ = "0.1.0"
__all__ = [
    "Agent", "AgentResult", "AgentTask", "ArtifactRef", "Config", "LLM", "LLMResponse",
    "PlanTask", "ResearchPlan", "RunDecision", "ScriptedLLM", "ToolCall", "default_tools",
]

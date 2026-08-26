"""Quant Research & Governance Agent — minimal, auditable agent kernel."""
from .agent import Agent
from .config import Config
from .llm import LLM, LLMResponse, ScriptedLLM, ToolCall
from .tools import default_tools

__version__ = "0.1.0"
__all__ = ["Agent", "Config", "LLM", "LLMResponse", "ScriptedLLM", "ToolCall", "default_tools"]

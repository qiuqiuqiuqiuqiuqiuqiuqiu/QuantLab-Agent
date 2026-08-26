"""Explicit configuration for CLI, API, and single-node deployment."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass
class Config:
    model: str = "~openai/gpt-latest"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    max_context_tokens: int = 64_000
    max_rounds: int = 30
    max_backtest_retries: int = 2
    workspace: Path = Path.cwd()
    audit_log: Path = Path(".quant-agent/audit.jsonl")
    database_path: Path = Path(".quant-agent/quantlab.db")
    auth_secret: str = "development-only-change-me"
    auth_ttl_minutes: int = 480
    environment: str = "development"
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        workspace = Path(os.getenv("QUANT_AGENT_WORKSPACE", Path.cwd())).resolve()
        environment = os.getenv("QUANTLAB_ENV", "development")
        cors = tuple(item.strip() for item in os.getenv("QUANTLAB_CORS_ORIGINS", "http://localhost:5173").split(",") if item.strip())
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        # Keep the generic variables as fallbacks so existing self-hosted/OpenAI-compatible
        # deployments do not break, but prefer OpenRouter for this project.
        api_key = openrouter_key or os.getenv("QUANT_AGENT_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("QUANT_AGENT_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        if not base_url and openrouter_key:
            base_url = "https://openrouter.ai/api/v1"
        return cls(
            model=os.getenv("QUANT_AGENT_MODEL", "~openai/gpt-latest"),
            api_key=api_key,
            base_url=base_url,
            max_tokens=int(os.getenv("QUANT_AGENT_MAX_TOKENS", "4096")),
            max_context_tokens=int(os.getenv("QUANT_AGENT_MAX_CONTEXT", "64000")),
            max_rounds=int(os.getenv("QUANT_AGENT_MAX_ROUNDS", "30")),
            max_backtest_retries=int(os.getenv("QUANT_AGENT_MAX_BACKTEST_RETRIES", "2")),
            workspace=workspace,
            audit_log=Path(os.getenv("QUANT_AGENT_AUDIT_LOG", workspace / ".quant-agent/audit.jsonl")).resolve(),
            database_path=Path(os.getenv("QUANTLAB_DATABASE_PATH", workspace / ".quant-agent/quantlab.db")).resolve(),
            auth_secret=os.getenv("QUANTLAB_AUTH_SECRET", "development-only-change-me"),
            auth_ttl_minutes=int(os.getenv("QUANTLAB_AUTH_TTL_MINUTES", "480")),
            environment=environment,
            cors_origins=cors,
            bootstrap_admin_username=os.getenv("QUANTLAB_BOOTSTRAP_ADMIN"),
            bootstrap_admin_password=os.getenv("QUANTLAB_BOOTSTRAP_PASSWORD"),
        )

"""Server entry point. Development reload is disabled in production."""
from ..config import Config


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Install web dependencies: pip install -e '.[web]'") from exc
    config = Config.from_env(load_dotenv=True)
    uvicorn.run(
        "quant_lab_agent.web.api:app", host="0.0.0.0", port=8000,
        reload=config.environment != "production", proxy_headers=config.environment == "production",
    )

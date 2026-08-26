from quant_lab_agent.config import Config


def test_openrouter_key_selects_openrouter_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.delenv("QUANT_AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    config = Config.from_env()

    assert config.api_key == "test-openrouter-key"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.model == "~openai/gpt-latest"


def test_explicit_base_url_overrides_openrouter_default(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("QUANT_AGENT_BASE_URL", "https://example.test/v1")

    assert Config.from_env().base_url == "https://example.test/v1"

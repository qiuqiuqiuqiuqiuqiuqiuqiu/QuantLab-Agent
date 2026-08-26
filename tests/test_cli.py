import sys
from pathlib import Path

from quant_lab_agent import cli


def test_history_uses_configured_workspace_without_an_api_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("QUANT_AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("QUANT_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["quantlab", "history"])

    assert cli.main() == 0
    assert capsys.readouterr().out == ""


def test_inspect_prints_a_persisted_manifest(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("QUANT_AGENT_WORKSPACE", str(tmp_path))
    store = cli.RunStore(tmp_path)
    run_id = store.create("研究均值回归")
    monkeypatch.setattr(sys, "argv", ["quantlab", "inspect", run_id])

    assert cli.main() == 0
    output = capsys.readouterr().out
    assert run_id in output
    assert "研究均值回归" in output

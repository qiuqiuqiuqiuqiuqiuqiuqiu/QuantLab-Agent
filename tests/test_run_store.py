from pathlib import Path

from quant_lab_agent.runtime import RunStore


def test_run_store_persists_events_and_completion(tmp_path: Path):
    store = RunStore(tmp_path)
    run_id = store.create("研究一个均值回归信号")
    store.record_event(run_id, "tool_call", {"tool": "register_research"})
    store.finish(run_id, "完成")

    manifest = store.load(run_id)
    assert manifest["status"] == "completed"
    assert manifest["final_answer"] == "完成"
    assert "tool_call" in (tmp_path / ".quant-agent" / "runs" / run_id / "events.jsonl").read_text()

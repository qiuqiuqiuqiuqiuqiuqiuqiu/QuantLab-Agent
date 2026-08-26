from evals.runner import run


def test_builtin_backtest_evals_pass():
    summary = run()
    assert summary["failed"] == 0
    assert summary["total"] >= 5

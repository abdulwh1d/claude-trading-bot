from tradebot.sim import simulate


def test_simulation_runs_end_to_end(tmp_path):
    s = simulate(days=6, seed=2, workdir=tmp_path / "sim", quiet=True)
    assert s["decisions"] > 0 and s["analyst_calls"] < s["calls_the_article_loop_would_make"]
    assert s["injection_blocked"] >= 1                  # the hijacked toy analyst never got an order through
    assert (tmp_path / "sim" / "reports" / "scorecard.md").exists()


def test_simulated_kill_switch_leaves_nothing_open(tmp_path):
    s = simulate(days=6, seed=3, kill_day=4, workdir=tmp_path / "sim", quiet=True)
    assert s["killed_on"] is not None and s["open_positions_at_end"] == 0

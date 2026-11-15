"""Performance budget: 30 math channels at 20 Hz under 1% of one core."""

from hondalog.bench import expr_bench


def test_math_and_alarms_fit_the_cpu_budget():
    results = expr_bench.measure(n=1500, repeats=5)
    by_name = {r.name: r for r in results}
    math30 = next(r for r in results if r.name.startswith("math: 30"))
    assert math30.budget_us == 500
    # The brief's bar, measured: 30 channels, 20 Hz, under 1% CPU.
    assert math30.cpu_at_20hz < 0.01, math30
    # And with headroom, so a 3x slower laptop (the T430) still passes.
    assert math30.us_per_sample < 500 / 3, math30
    for r in by_name.values():
        assert r.ok, r


def test_whole_log_filter_is_interactive():
    """W4-05 wants a filter over a one-hour log re-rendered in under 200 ms."""
    times = expr_bench.measure_vector(repeats=2)
    assert times["filter: ECT > 80 and steady throttle"] < 200
    assert times["pure arithmetic"] < 50


def test_cli_runs(capsys):
    assert expr_bench.main(["-n", "300"]) == 0
    assert "CPU @ 20 Hz" in capsys.readouterr().out

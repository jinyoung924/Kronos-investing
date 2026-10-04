"""Stage 8 (docs/spec.md): comparison report, shortfall waterfall, run_report, run_pipeline.
The smoke tests use the fake run_ids on disk (skipped when they are not generated)."""
from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from common.config import cfg_override, load_config
from common.paths import Paths
from G_report import compare as cmp
from G_report import shortfall as sfl
from G_report.run_report import CONSTRAINT_ORDER, run_report

ROOT = Paths(load_config("configs/base.yaml"), ".").root
SET = ["--set", "backtest.delist_policy=last_close"]


def _shortfall_rows(order):
    """Synthetic shortfall_metrics rows of one strategy: v1 start, all_off equal to it, each constraint costs something."""
    rows = [{"engine": "v1", "strategy": "s", "scenario": "v1@no_costs", "cagr": 0.10, "sharpe": 1.00, "total_cost": 0.0},
            {"engine": "v2", "strategy": "s", "scenario": "all_off", "cagr": 0.10, "sharpe": 1.00, "total_cost": 0.0, "min_cash_share": 0.0}]
    drops = [0.030, 0.004, 0.0, 0.002, 0.0, 0.001]
    cagr, on = 0.10, []
    for c, d in zip(order, drops):
        on.append(c)
        cagr -= d
        rows.append({"engine": "v2", "strategy": "s", "scenario": sfl.canonical("+".join(on), CONSTRAINT_ORDER), "cagr": cagr, "sharpe": cagr * 10,
                     "total_cost": 0.03, "min_cash_share": 0.01})
    return pd.DataFrame(rows)


def test_waterfall_steps_sum_to_the_total_change():
    order = ["costs", "integer_shares", "cash", "price_limit", "halt", "liquidity"]
    wf = sfl.waterfall(_shortfall_rows(order), order, CONSTRAINT_ORDER)
    assert list(wf["step"]) == ["v1 (비용 없음)", "엔진 차이 (v2, 제약 없음)"] + [f"+ {c}" for c in order]
    assert wf["d_cagr"].sum() == pytest.approx(wf["cagr"].iloc[-1] - wf["cagr"].iloc[0])
    assert wf["d_sharpe"].sum() == pytest.approx(wf["sharpe"].iloc[-1] - wf["sharpe"].iloc[0])
    assert wf["d_cagr"].iloc[1] == 0.0 and sfl.largest_step(wf) == {"step": "+ costs", "d_cagr": pytest.approx(-0.030)}
    # another order: scenario names stay canonical, the total is the same
    order2 = ["cash", "costs", "integer_shares", "price_limit", "halt", "liquidity"]
    wf2 = sfl.waterfall(_shortfall_rows(order2), order2, CONSTRAINT_ORDER)
    assert list(wf2["scenario"])[2:4] == ["cash", "costs+cash"] and wf2["d_cagr"].sum() == pytest.approx(wf["d_cagr"].sum())
    with pytest.raises(KeyError, match="liquidity"):
        sfl.waterfall(_shortfall_rows(order).iloc[:-1], order, CONSTRAINT_ORDER)


def test_strategy_table_rows_labels_and_notes():
    pm = lambda run, bench, days: pd.DataFrame([  # noqa: E731
        {"run_id": run, "strategy": "topk@paper_costs", "strategy_base": "topk", "costs": "paper_costs", "cagr": 0.1, "benchmark": bench, "n_days": days},
        {"run_id": run, "strategy": "topk@no_costs", "strategy_base": "topk", "costs": "no_costs", "cagr": 0.2, "benchmark": bench, "n_days": days},
        {"run_id": run, "strategy": "topk", "strategy_base": "topk", "costs": "kr", "cagr": 0.05, "benchmark": bench, "n_days": days},
        {"run_id": run, "strategy": "index:KOSPI", "strategy_base": "index:KOSPI", "costs": "none", "cagr": 0.07}])
    t = cmp.strategy_table({"a": pm("a", "equal_weight", 240), "b": pm("b", "KOSPI", 230)})
    assert list(t["row"]) == ["topk@a@paper_costs", "topk@a", "topk@b@paper_costs", "topk@b", "index:KOSPI"]      # no cost-free rows, the index once
    assert cmp.strategy_table({"a": pm("a", "equal_weight", 240)}, ["other"])["row"].tolist() == ["index:KOSPI"]
    notes = cmp.consistency_notes(t, {"topk@a": ("2024-07-01", "2025-06-30"), "topk@b": ("2024-07-01", "2025-06-13")})
    assert len(notes) == 3 and "벤치마크" in notes[1]
    assert cmp.consistency_notes(cmp.strategy_table({"a": pm("a", "equal_weight", 240)}), {}) == []
    assert cmp.fmt(0.1234, "cagr") == "+12.3%" and cmp.fmt(float("nan"), "ir") == "—" and cmp.fmt(-0.0342, "d_cagr") == "-3.42%p"


def _numbers(md: str) -> set[str]:
    return {c.strip() for line in md.splitlines() if line.startswith("|") for c in line.strip("|").split("|")}


def test_report_for_two_fake_run_ids_reads_only_f_metrics():
    cfg = cfg_override(load_config(ROOT / "configs/base.yaml"), {"backtest.delist_policy": "last_close"})
    paths = Paths(cfg, ROOT)
    runs = ["fake_dummy_base", "fake_dummy_paper"]
    if not all((paths.metrics_dir(r) / "portfolio_metrics.csv").exists() for r in runs):
        pytest.skip("run F_evaluate.run_evaluate for fake_dummy_base and fake_dummy_paper first")
    out = run_report(cfg, runs, None, "test_stage8", None, ROOT, log=lambda *_: None)
    d = out["out_dir"]
    md = (d / "compare.md").read_text(encoding="utf-8")
    for r in runs:
        pm = pd.read_csv(paths.metrics_dir(r) / "portfolio_metrics.csv")
        for row in pm[(pm["costs"] != "no_costs") & ~pm["strategy"].str.startswith("index:")].to_dict("records"):
            label = cmp.row_label(row["strategy_base"], r, row["costs"])
            assert label in md                                                              # every strategy row of both run_ids
            line = next(x for x in md.splitlines() if x.startswith(f"| {label} |"))
            for col in ("cagr", "sharpe", "max_drawdown", "annual_turnover", "deflated_sharpe"):
                assert cmp.fmt(row[col], col) in line                                       # the number printed is the one in F_metrics
        sig = json.loads((paths.metrics_dir(r) / "signal_metrics.json").read_text())
        assert cmp.fmt(sig["ic"]["exp_ret"]["mean"], "ic_mean") in _numbers(md)
    assert (d / "figures/nav.png").stat().st_size > 10_000 and (d / "figures/quantile_returns.png").stat().st_size > 10_000
    assert (d / "shortfall.md").exists() and (d / "meta.json").exists()
    assert "기간이 다르다" not in md or "주의" in md
    with pytest.raises(FileNotFoundError):
        run_report(cfg, ["no_such_run"], None, "test_stage8", None, ROOT, log=lambda *_: None)
    assert run_report(cfg, ["fake_dummy_base", "no_such_run"], None, "test_stage8", None, ROOT, explicit=False, log=lambda *_: None)["skipped"] == ["no_such_run"]


def test_report_code_does_not_import_metric_functions():
    """The report reads numbers; it must not compute performance metrics (no F_evaluate import, no return math)."""
    for name in ("compare.py", "shortfall.py", "run_report.py"):
        src = (ROOT / "G_report" / name).read_text(encoding="utf-8")
        assert "from F_evaluate" not in src and "import F_evaluate" not in src
        for banned in ("pct_change", ".std(", "np.sqrt", "cumprod", "performance_summary"):
            assert banned not in src, f"{name} uses {banned}"


def test_run_pipeline_runs_c_to_g_for_equal_weight():
    cfg = load_config(ROOT / "configs/base.yaml")
    paths = Paths(cfg, ROOT)
    if not paths.manifest_file("fake_dummy_base").exists() or not paths.prediction_file("fake_dummy_base", "2024-07-01").exists():
        pytest.skip("run B_model_infer.make_fake_predictions --kind dummy first")
    try:
        r = subprocess.run([sys.executable, "scripts/run_pipeline.py", "--run-id", "fake_dummy_base", "--strategy", "equal_weight", "--costs", "paper", *SET],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert [line[:3] for line in r.stdout.splitlines() if line.startswith("[") and "done" in line] == ["[C]", "[D]", "[E]", "[E]", "[F]", "[G]"]
        assert (paths.report_dir("fake_dummy_base") / "compare.md").exists()
        assert "equal_weight@fake_dummy_base@paper_costs" in (paths.report_dir("fake_dummy_base") / "compare.md").read_text(encoding="utf-8")
        # a failing stage stops the run and is named
        bad = subprocess.run([sys.executable, "scripts/run_pipeline.py", "--run-id", "fake_dummy_base", "--strategy", "no_such_strategy", "--from", "D", *SET],
                             cwd=ROOT, capture_output=True, text=True)
        assert bad.returncode != 0 and "stage D failed" in bad.stderr and "[E]" not in bad.stdout
    finally:      # the single-strategy run rewrote portfolio_metrics.csv with one strategy: score every strategy again
        subprocess.run([sys.executable, "-m", "F_evaluate.run_evaluate", "--run-id", "fake_dummy_base", "--strategy", "all", *SET], cwd=ROOT, capture_output=True)

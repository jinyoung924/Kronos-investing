#!/usr/bin/env python
"""G_report entry point: data/F_metrics/{run_id} (+ NAV series of data/E_backtest for the charts) -> reports/{name}/.

    python -m G_report.run_report --run-id kronos_base_v1,kronos_paper_v1 [--strategy a,b|all] [--name NAME]
                                  [--shortfall-strategy a,b] [--config configs/base.yaml] [--root .] [--set ...]

compare.md   per run_id: IC summary of exp_ret / exp_ret_mean and the naive controls + quantile-return chart (run_ids are
             not pooled: H differs); one strategy table with rows {strategy}@{run_id}[@costs]; cumulative NAV chart
             (engine v1 with costs, equal_weight and index lines, one line style per run_id).
shortfall.md per (run_id, strategy) with engine v2 scenarios scored by `F_evaluate.run_evaluate --engine v2`:
             table and waterfall chart from v1 without costs through the constraints in report.shortfall_order.
Without --run-id the list is report.compare_run_ids; run_ids of that list without metrics are skipped and named in the
report, a run_id passed on the command line must exist. Output folder = --name or the first run_id.
Tables hold only numbers read from F_metrics. The only module of the stage that touches files. Imports only `common`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.config import cfg_get, cfg_override, load_config, parse_set_overrides, require  # noqa: E402
from common.meta import write_meta  # noqa: E402
from common.paths import Paths  # noqa: E402
from G_report import compare as cmp  # noqa: E402
from G_report import shortfall as sfl  # noqa: E402

STAGE = "G_report"
CONSTRAINT_ORDER = ["costs", "integer_shares", "cash", "price_limit", "halt", "liquidity"]     # scenario folder names use this order
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
PAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#7a6fd0", "#8a8984", "#3b9aa8"]
LINESTYLES = ["-", "--", ":", "-."]
IC_HEAD = {"signal": "시그널", "ic_mean": "IC 평균", "ic_std": "IC 표준편차", "icir": "ICIR", "t": "t", "share_positive": "양수 비율", "n_dates": "날짜 수"}
ST_HEAD = {"row": "전략@run_id", "cagr": "CAGR", "aer": "AER", "sharpe": "Sharpe", "max_drawdown": "MDD", "ir": "IR", "annual_turnover": "연 턴오버",
           "names_changed_per_day": "하루 교체 수", "cost_drag_cagr": "비용 전후 차이", "sharpe_ci": "Sharpe 95% 구간", "deflated_sharpe": "DSR",
           "n_trials": "시도 수", "benchmark": "벤치마크"}
WF_HEAD = {"step": "단계", "scenario": "시나리오", "cagr": "CAGR", "sharpe": "Sharpe", "d_cagr": "ΔCAGR", "d_sharpe": "ΔSharpe",
           "total_cost": "총비용", "min_cash_share": "최저 현금 비중"}


def _style(ax, title):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=10, color=INK, fontweight="bold", pad=8)


def load_run(paths: Paths, run_id: str) -> dict | None:
    d = paths.metrics_dir(run_id)
    if not (d / "signal_metrics.json").exists() or not (d / "portfolio_metrics.csv").exists():
        return None
    sf = d / "shortfall_metrics.csv"
    return {"signal": json.loads((d / "signal_metrics.json").read_text(encoding="utf-8")), "portfolio": pd.read_csv(d / "portfolio_metrics.csv"),
            "shortfall": pd.read_csv(sf) if sf.exists() else None, "dir": d}


def quantile_figure(runs: dict, out: Path) -> Path:
    fig, axes = plt.subplots(1, len(runs), figsize=(5.4 * len(runs), 3.4), facecolor=SURFACE, squeeze=False)
    for ax, (run_id, r) in zip(axes[0], runs.items()):
        s = r["signal"]
        q = s["quantiles"]["exp_ret"]["mean_by_quantile"]
        _style(ax, f"{run_id}: exp_ret 분위별 평균 실현수익률 (H {s['H']})")
        ax.bar(list(q), list(q.values()), color=PAL[0], width=0.6)
        for i, v in enumerate(q.values()):
            ax.annotate(f"{v:+.2%}", (i, v), xytext=(0, 4 if v >= 0 else -10), textcoords="offset points", ha="center", fontsize=8, color=INK2)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=2)); ax.axhline(0, color=GRID, lw=1)
    fig.tight_layout(); fig.savefig(out, dpi=130, facecolor=SURFACE); plt.close(fig)
    return out


def nav_figure(paths: Paths, cfg: dict, table: pd.DataFrame, out: Path) -> tuple[Path, dict]:
    """Cumulative NAV of the table's strategy rows (engine v1 result folders), one line style per run_id, plus index lines."""
    fig, ax = plt.subplots(figsize=(9.5, 4.4), facecolor=SURFACE)
    _style(ax, "누적 NAV (엔진 v1, 비용 포함). 선 모양 = run_id")
    run_ids = list(dict.fromkeys(table.loc[~table["row"].astype(str).str.startswith("index:"), "run_id"]))
    colors, periods, dates = {}, {}, None
    for r in table.to_dict("records"):
        if str(r["row"]).startswith("index:"):
            continue
        folder = r["strategy"] + ("" if r["costs"] == "kr" else f"@{r['costs']}")
        f = paths.backtest_dir(r["run_id"], "v1", folder) / "nav.csv"
        if not f.exists():
            continue
        nav = pd.read_csv(f, parse_dates=["date"]).set_index("date")["nav"]
        periods[r["row"]] = (str(nav.index[0].date()), str(nav.index[-1].date()))
        color = colors.setdefault(r["strategy"], PAL[len(colors) % len(PAL)])
        ax.plot(nav.index, nav, color=color, lw=1.6, ls=LINESTYLES[run_ids.index(r["run_id"]) % len(LINESTYLES)], label=f"{r['row']}  {nav.iloc[-1]:.2f}")
        dates = nav.index if dates is None else dates
    bench_file = paths.prepared_path("benchmark")
    if dates is not None and bench_file.exists():
        bench = pd.read_parquet(bench_file)
        for tk in cfg_get(cfg, "evaluate.reference_indices", []) or []:
            c = bench[bench["ticker"] == tk].set_index("date")["close"].reindex(dates).ffill()
            if c.notna().any():
                c = c / c.dropna().iloc[0]
                ax.plot(c.index, c, color=INK, lw=1.0, alpha=0.55, ls=(0, (1, 1)), label=f"index:{tk}  {c.iloc[-1]:.2f}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m")); ax.legend(frameon=False, fontsize=7, labelcolor=INK2, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout(); fig.savefig(out, dpi=130, facecolor=SURFACE); plt.close(fig)
    return out, periods


def waterfall_figure(wf: pd.DataFrame, title: str, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8.4, 3.6), facecolor=SURFACE)
    _style(ax, title)
    x = range(len(wf))
    first = float(wf["cagr"].iloc[0])
    ax.bar(0, first, color=PAL[6], width=0.6)
    for i in range(1, len(wf)):
        prev, cur = float(wf["cagr"].iloc[i - 1]), float(wf["cagr"].iloc[i])
        ax.bar(i, cur - prev, bottom=prev, color=PAL[1] if cur < prev else PAL[2], width=0.6)
        ax.plot([i - 0.7, i - 0.3], [prev, prev], color=INK2, lw=0.8)
    for i, r in enumerate(wf.to_dict("records")):
        txt = f"{r['cagr']:+.1%}" if i == 0 else f"{r['d_cagr'] * 100:+.2f}%p"
        ax.annotate(txt, (i, r["cagr"]), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=8, color=INK2)
    ax.axhline(float(wf["cagr"].iloc[-1]), color=INK2, lw=0.8, ls=":")
    ax.set_xticks(list(x)); ax.set_xticklabels([s.replace(" (", "\n(") for s in wf["step"]], fontsize=7)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=1)); ax.axhline(0, color=GRID, lw=1)
    fig.tight_layout(); fig.savefig(out, dpi=130, facecolor=SURFACE); plt.close(fig)
    return out


def build_compare(paths: Paths, cfg: dict, runs: dict, skipped: list[str], strategies: list[str] | None, fig_dir: Path) -> str:
    table = cmp.strategy_table({k: v["portfolio"] for k, v in runs.items()}, strategies)
    qf = quantile_figure(runs, fig_dir / "quantile_returns.png")
    nf, periods = nav_figure(paths, cfg, table, fig_dir / "nav.png")
    lines = ["# 전략 비교 리포트", "", f"run_id: {', '.join(runs)}. 엔진 v1. 모든 수치는 data/F_metrics/{{run_id}}/ 에서 읽었다.", ""]
    if skipped:
        lines += [f"> 결과가 없어 빠진 run_id: {', '.join(skipped)} (F_evaluate 산출물 없음).", ""]
    lines += ["## 시그널 평가 (run_id별, H가 달라 합치지 않는다)", ""]
    for run_id, r in runs.items():
        s = r["signal"]
        q = s["quantiles"]["exp_ret"]
        lines += [f"### {run_id} (프로필 {s.get('profile')}, H {s['H']})", "", cmp.md_table(cmp.ic_table(s), IC_HEAD), "",
                  f"- exp_ret 분위 스프레드(상위 − 하위) {q['mean_spread'] * 100:+.2f}%p (t {q['t']:.2f}), 적중률 {s['hit_rate']['hit_rate']:.1%}, "
                  f"캘리브레이션 {s['calibration'].get('spearman', float('nan')):+.2f}", ""]
    lines += [f"![분위별 실현수익률](figures/{qf.name})", "", "## 전략 비교", ""]
    show = table.copy()
    show["sharpe_ci"] = [("—" if pd.isna(a) else f"[{a:.2f}, {b:.2f}]") for a, b in zip(show["sharpe_ci_lower"], show["sharpe_ci_upper"])]
    cols = ["row", "cagr", "aer", "sharpe", "max_drawdown", "ir", "annual_turnover", "names_changed_per_day", "cost_drag_cagr", "sharpe_ci", "deflated_sharpe", "n_trials", "benchmark"]
    lines += [cmp.md_table(show[cols], ST_HEAD), ""]
    lines += ["- 행 이름의 `@paper_costs`는 논문 비용(매수 0.10%, 매도 0.15%) 시나리오다. 접미사가 없으면 한국 비용이다.",
              "- AER·IR의 기준은 `벤치마크` 열이다. `index:` 행은 지수 종가 Buy & Hold 참고선이라 상대 지표가 없다.",
              "- 비용 전후 차이 = 같은 전략의 비용 없는 실행 CAGR − 이 행의 CAGR. DSR의 시도 수는 `시도 수` 열이다."]
    lines += [f"- 주의: {n}" for n in cmp.consistency_notes(table, periods)]
    lines += ["", f"![누적 NAV](figures/{nf.name})", ""]
    return "\n".join(lines)


def build_shortfall(cfg: dict, runs: dict, wanted: list[str] | None, fig_dir: Path) -> str:
    order = list(require(cfg, "report.shortfall_order"))
    have = [(run_id, s) for run_id, r in runs.items() if r["shortfall"] is not None for s in sorted(r["shortfall"]["strategy"].unique())]
    chosen = [(r, s) for r, s in have if wanted is None or s in wanted]
    lines = ["# Shortfall 분해 리포트", "",
             "엔진 v1(비용 없음)에서 출발해 엔진 v2에서 제약을 하나씩 켤 때 CAGR과 Sharpe가 얼마나 변하는지 본다. "
             "수치는 data/F_metrics/{run_id}/shortfall_metrics.csv에서 읽었다.", "",
             f"- **켜는 순서**: {' → '.join(order)} (`report.shortfall_order`).",
             "- **순서가 결과를 바꾼다.** 각 단계의 감소분은 앞에서 이미 켠 제약에 따라 달라진다. 순서를 바꾸면 개별 숫자는 달라지고 합계만 같다.",
             "- **엔진 차이** 행은 v1(비용 없음)과 제약을 모두 끈 v2의 차이다. 0에 가까워야 두 엔진이 같은 것을 계산한다는 뜻이다.", ""]
    if wanted and not chosen:
        lines += [f"> 설정의 대상 전략({', '.join(wanted)})은 v2 시나리오 결과가 없다. 결과가 있는 전략 전부를 대신 실었다.", ""]
        chosen = have
    if not chosen:
        return "\n".join(lines + ["v2 시나리오 결과가 없다. `python -m E_backtest.run_backtest --engine v2 --shortfall` 과 "
                                  "`python -m F_evaluate.run_evaluate --engine v2`를 먼저 실행한다.", ""])
    for run_id, strat in chosen:
        sf = runs[run_id]["shortfall"]
        wf = sfl.waterfall(sf[sf["strategy"] == strat], order, CONSTRAINT_ORDER)
        f = waterfall_figure(wf, f"{strat}@{run_id}: 제약을 켤 때의 CAGR 변화", fig_dir / f"shortfall_{run_id}_{strat}.png")
        big = sfl.largest_step(wf)
        v2 = sf[(sf["strategy"] == strat) & (sf["engine"] == "v2")]
        init_cash = v2["init_cash"].dropna().iloc[0] if "init_cash" in v2 and v2["init_cash"].notna().any() else None
        cost_scn = v2.loc[v2["cost_scenario"].astype(str) != "none", "cost_scenario"]
        lines += [f"## {strat}@{run_id}", "", cmp.md_table(wf, WF_HEAD), "",
                  f"- 처음(v1, 비용 없음) {wf['cagr'].iloc[0] * 100:+.2f}% → 끝(모든 제약) {wf['cagr'].iloc[-1] * 100:+.2f}%: 합계 {(wf['cagr'].iloc[-1] - wf['cagr'].iloc[0]) * 100:+.2f}%p.",
                  f"- 가장 큰 감소: **{big['step']}** ({big['d_cagr'] * 100:+.2f}%p). 엔진 차이 {wf['d_cagr'].iloc[1] * 100:+.4f}%p.",
                  f"- 비용 시나리오 {cost_scn.iloc[0] if len(cost_scn) else '—'}, 초기 자본 {init_cash:,.0f}원." if init_cash else "", "",
                  f"![워터폴](figures/{f.name})", ""]
    return "\n".join(lines)


def run_report(cfg: dict, run_ids: list[str], strategies: list[str] | None, name: str | None, shortfall_strategies: list[str] | None,
               root: Path, explicit: bool = True, log=print) -> dict:
    paths = Paths(cfg, root)
    plt.rcParams.update({"font.family": ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"], "axes.unicode_minus": False})
    runs, skipped = {}, []
    for r in run_ids:
        loaded = load_run(paths, r)
        if loaded is None:
            if explicit:
                raise FileNotFoundError(f"no F_metrics for run_id {r!r} under {paths.metrics_dir(r)}: run F_evaluate.run_evaluate first")
            skipped.append(r)
        else:
            runs[r] = loaded
    if not runs:
        raise FileNotFoundError(f"none of the run_ids {run_ids} has F_metrics")
    out_dir = paths.report_dir(name or next(iter(runs)))
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "compare.md").write_text(build_compare(paths, cfg, runs, skipped, strategies, fig_dir), encoding="utf-8")
    (out_dir / "shortfall.md").write_text(build_shortfall(cfg, runs, shortfall_strategies, fig_dir), encoding="utf-8")
    inputs = {f"{k}:{f}": v["dir"] / f for k, v in runs.items() for f in ("signal_metrics.json", "portfolio_metrics.csv", "shortfall_metrics.csv") if (v["dir"] / f).exists()}
    write_meta(out_dir, cfg, inputs=inputs, root=root, extra={"stage": STAGE, "run_id": ",".join(runs), "strategy": ",".join(strategies) if strategies else "all",
                                                             "engine": "v1+v2", "skipped_run_ids": skipped, "figures": sorted(p.name for p in fig_dir.glob("*.png"))})
    log(f"wrote {out_dir}/compare.md, shortfall.md, figures/ ({len(list(fig_dir.glob('*.png')))} png)" + (f"; skipped {skipped}" if skipped else ""))
    return {"out_dir": out_dir, "run_ids": list(runs), "skipped": skipped}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", help="comma-separated run_ids (default report.compare_run_ids)")
    p.add_argument("--strategy", default="all", help="comma-separated strategy names for the comparison rows, or all")
    p.add_argument("--name", help="report folder name (default: the first run_id)")
    p.add_argument("--shortfall-strategy", help="comma-separated strategies for shortfall.md (default report.shortfall_strategy)")
    p.add_argument("--config", default="configs/base.yaml")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    a = p.parse_args(argv)
    root = Path(a.root)
    cfg = cfg_override(load_config(root / a.config), parse_set_overrides(a.set))
    run_ids = [x.strip() for x in a.run_id.split(",") if x.strip()] if a.run_id else list(require(cfg, "report.compare_run_ids"))
    strategies = None if a.strategy.strip() == "all" else [x.strip() for x in a.strategy.split(",") if x.strip()]
    sf = a.shortfall_strategy or cfg_get(cfg, "report.shortfall_strategy")
    run_report(cfg, run_ids, strategies, a.name, [x.strip() for x in str(sf).split(",") if x.strip()] if sf else None, root, explicit=bool(a.run_id))


if __name__ == "__main__":
    main()

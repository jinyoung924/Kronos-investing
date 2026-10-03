"""F_evaluate: signals + engine results -> prediction metrics and portfolio metrics (data/F_metrics/{run_id}/).

labels.py             realised returns (the only place that computes them): next open -> open H days later
signal_metrics.py     rank IC, ICIR, quantile returns, hit rate, calibration, naive controls, regimes
portfolio_metrics.py  CAGR, vol, Sharpe, Sortino, Calmar, MDD, monthly, AER/beta/alpha/TE/IR, turnover, cost drag
significance.py       circular block bootstrap Sharpe CI, deflated Sharpe (Bailey & López de Prado 2014)
run_evaluate.py       entry point: --run-id --strategy a,b|all [--engine v1]
Imports only `common`.
"""

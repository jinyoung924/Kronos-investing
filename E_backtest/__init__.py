"""E_backtest: target weights (data/D_weights) + prepared prices -> fills, holdings, NAV (data/E_backtest/{run_id}/{engine}/{strategy}/).

costs.py               CostModel(cfg, scenario): kr | paper | none
engine_v1_weights.py   run_v1: weight space, next-open fill, close valuation, drift between fills (Stage 4)
run_backtest.py        entry point: --run-id --strategy a,b|all --engine v1 [--costs kr|paper] [--no-costs]
Imports only `common`; never imports D_strategy (weights arrive as files).
"""

"""KRX Open API -> lookahead-safe backtest dataset (data/raw, data/universe).

Stages (data_prepare/run.py):
  fetch  : cache one JSON per (endpoint, date) under data/krx_raw/ (resumable, 10k calls/day quota)
  build  : raw JSON -> data/raw/kospi/prices.parquet, data/raw/benchmark/prices.parquet,
           data/universe/constituents_kospi.parquet, plus a validation summary
See docs/data_pipeline.md.
"""

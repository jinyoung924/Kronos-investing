"""A_data_prepare: KRX Open API -> collected dataset (data/raw, data/universe) -> prepared tables (data/A_prepared).

Collection (docs/data_pipeline.md, `python -m A_data_prepare.run [step ...]`):
  fetch  : cache one JSON per (endpoint, date) under data/krx_raw/<snapshot>/ (resumable, 10k calls/day quota)
  build  : raw JSON -> data/raw/{kospi,kosdaq}/prices.parquet, adjustment_events.csv, data/universe/constituents_*
  benchmark, sanity, manifest
Stage 1 (docs/spec.md, `python -m A_data_prepare.run_prepare`):
  build_prices / build_calendar / build_adj_factor / build_universe -> data/A_prepared/{prices,calendar,halts,
  adj_factor,events,universe,benchmark}.parquet + meta.json. Imports only `common`.
"""

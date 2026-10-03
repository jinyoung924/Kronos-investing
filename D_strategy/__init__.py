"""D_strategy: signals (data/C_signals/{run_id}) -> target weights per rebalance date (data/D_weights/{run_id}/{strategy}.parquet).

base.py            Strategy interface + helpers (equal_weights, top_k, check_weights)
registry.py        auto-discovery of one-strategy-per-file modules, resolve("a,b" | "all"), get_strategy(name, cfg)
<name>.py          one strategy each; name == file name == configs strategies key
run_strategy.py    entry point: --run-id --strategy a,b|all
Imports only `common`. Strategies never see prices or labels.
"""

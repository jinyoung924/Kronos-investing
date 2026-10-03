"""C_signal: prediction samples (data/B_predictions/{run_id}) -> per (as_of_date, ticker) signals (data/C_signals/{run_id}).

aggregate.py          exp_ret, exp_ret_mean, std, p_up, pred_range, n_samples (first signal.n_samples samples, D-11)
baseline_features.py  mom20, vol20, rev5 from adjusted close up to as_of only
run_signal.py         entry point: universe ∩ predicted tickers per date (D-3), last_close on data.price_basis (D-1)
Imports only `common`.
"""

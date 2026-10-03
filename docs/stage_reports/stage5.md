# Stage 5 보고서

2026-10-03. 범위: docs/spec.md "Stage 5. F_evaluate — 채점" + 사용자 지시(비용·K를 논문 investment simulation 값으로 기본 설정 → docs/decisions.md D-15). 다음 Stage는 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

**D-15 반영**

| 항목 | 반영 |
| --- | --- |
| 비용 기본값 | `costs.scenario: paper`(매수 0.10%, 매도 0.15%, 세금·슬리피지 없음). 기본 결과 폴더가 `@paper_costs`가 된다. `--costs kr`은 세율표가 채워질 때까지 오류 |
| K 기본값 | `strategies.momentum20_topk.k`, `random_topk.k`, `vol_target.k`, `topk.k` = 50, `topk.n_drop` = 5 (논문 CSI 300 / 공식 레포 n_symbol_hold 50·drop 5). D-14의 `--set`로 바꿔 돌린다 |
| 재생성 | fake_dummy_base·fake_oracle_base의 D_weights(세 벤치마크, k 50)와 E_backtest v1(@paper_costs, @no_costs) 다시 생성. delist_policy는 여전히 `--set backtest.delist_policy=last_close` |

**F_evaluate**

| 파일 | 설명 |
| --- | --- |
| labels.py | `compute_labels(prices, adj_factor, calendar, as_of_dates, horizon, tickers)`: label = Open_adj(f+H)/Open_adj(f) − 1, f = next_trading_day(s); label_mean = mean_{h=1..H} Open_adj(f+h)/Open_adj(f) − 1 (exp_ret_mean 채점용). 라벨을 계산하는 유일한 곳 |
| signal_metrics.py | `rank_ic`(날짜별 Spearman, 최소 표본 수), `ic_summary`(mean, std, ICIR, t, 양수 비율), `quantile_returns`(날짜 × 분위, Q5−Q1 스프레드와 t), `hit_rate`(p_up > 0.5 vs 라벨 부호), `calibration`(std vs \|label − exp_ret\| Spearman), `regime_labels`(반기, 벤치마크 라벨 구간 수익률 부호로 up/down), `ic_by_regime` |
| portfolio_metrics.py | `cagr`, `ann_vol`, `sharpe`, `sortino`, `max_drawdown`(peak·trough·recovery·회복 행수), `calmar`, `monthly_returns/distribution`, `relative_metrics`(AER·beta·alpha·TE·IR·월별 hit), `trading_metrics`(연 턴오버, 체결당 교체율, 평균 보유, 체결당·하루당 교체 종목 수, 총비용), `performance_summary` |
| significance.py | `block_bootstrap_sharpe_ci`(원형 블록 부트스트랩, 부록 B 방식), `probabilistic_sharpe`, `expected_max_sharpe`, `deflated_sharpe`(Bailey & López de Prado 2014, docstring에 식·출처), `deflated_sharpe_from_returns` |
| run_evaluate.py | `--run-id --strategy a,b\|all [--engine v1] [--set]`. 시그널 채점(exp_ret, exp_ret_mean + naive mom20·rev5, 랜덤워크는 IC 정의 불가로 명시) → E 결과 폴더별 포트폴리오 지표(벤치마크 = `evaluate.benchmark`: equal_weight면 같은 엔진·비용 시나리오의 equal_weight 폴더, 지수 티커면 종가 B&H) + 참고선 `index:KOSPI`, `index:KOSDAQ` 행 + 비용 전후 CAGR(같은 전략의 @no_costs 쌍) + Sharpe 95% 부트스트랩 구간 + Deflated Sharpe(n_trials = 그 run_id trials.csv 행 수) → data/F_metrics/{run_id}/ signal_metrics.json, ic_timeseries.csv, quantile_returns.csv, portfolio_metrics.csv, trials.csv(같은 (run_id, engine, strategy, config_hash)는 한 번만), meta.json |
| configs/base.yaml | `evaluate.benchmark: equal_weight`(D-6 잠정), `reference_indices: [KOSPI, KOSDAQ]`, `periods_per_year: 252`, `risk_free: 0.0`, `min_cross_section: 10`, bootstrap seed = project.seed |
| tests/test_stage5_metrics.py | 9개 (아래). quantstats 0.0.86으로 교차검증(없으면 모듈 전체 skip) |
| scripts/probes/stage5_figures.py | 분위별 수익률 막대, 벤치마크 전략·지수 누적 NAV, IC 요약표·성과표 출력 |
| README.md | 실행 명령·구조 |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest -W error::FutureWarning                       88 passed in 27.78s  (Stage 4까지 79 + test_stage5_metrics.py 9)
python -m D_strategy.run_strategy --run-id {fake_dummy_base,fake_oracle_base} --strategy all           (k 50)
python -m E_backtest.run_backtest --run-id {…} --strategy all [--no-costs] --set backtest.delist_policy=last_close
python -m F_evaluate.run_evaluate --run-id fake_oracle_base --strategy all     1.1초
python -m F_evaluate.run_evaluate --run-id fake_dummy_base  --strategy all     1.0초
python scripts/probes/stage5_figures.py
```

**IC 요약 (H 5, 리밸런싱일 49, 날짜별 Spearman)**

| run_id | 시그널 | IC 평균 | IC 표준편차 | ICIR | t | 적중률 (p_up) | Q5−Q1 스프레드 (t) | 캘리브레이션 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fake_oracle_base | exp_ret | 0.945 | 0.022 | 42.9 | 300 | 0.918 | +19.75%p (55.1) | +0.043 |
| fake_oracle_base | exp_ret_mean | 0.926 | 0.028 | 33.2 | 233 | — | — | — |
| fake_dummy_base | exp_ret | 0.001 | 0.031 | 0.03 | 0.2 | 0.508 | +0.07%p (0.5) | +0.006 |
| fake_dummy_base | exp_ret_mean | −0.001 | 0.029 | −0.05 | −0.3 | — | — | — |
| (naive) mom20 | 라벨 vs mom20 | −0.051 | 0.133 | −0.39 | −2.7 | — | — | — |
| (naive) rev5 | 라벨 vs rev5 | +0.034 | 0.125 | 0.27 | 1.9 | — | — | — |

- 오라클 IC가 1이 아니라 0.945인 이유: 오라클은 as_of 종가 → 5거래일 뒤 종가를 알고, 라벨은 다음날 시가 → 그 5거래일 뒤 시가다(D-10의 기대와 일치; 하루 어긋남). 레짐별로도 0.942 ~ 0.947로 균일하다.
- 더미는 IC·적중률·분위 스프레드 모두 0 근처(정보 없음). naive 대조군: 20일 모멘텀은 음의 IC(단기 반전), 5일 반전은 약한 양의 IC. 이 기간·유니버스에서 Stage 4의 momentum20_topk −63%와 방향이 같다.

**벤치마크 전략 성과 (fake_dummy_base, v1; 벤치마크 = equal_weight 같은 비용 시나리오)**

| 전략 | CAGR | 연변동성 | Sharpe | Sortino | MDD | AER | beta | IR | 연 턴오버 | 평균 보유 | 비용 드래그 (CAGR) | Sharpe 95% CI | DSR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| equal_weight@paper_costs | −6.9% | 25.3% | −0.15 | −0.20 | −26.2% | 0 | 1.00 | — | 4.9 | 857 | 1.1%p | [−1.66, 1.54] | 0.07 |
| momentum20_topk@paper_costs | −65.3% | 33.5% | −2.98 | −3.65 | −67.0% | −58.4%p | 0.91 | −3.92 | 24.7 | 50.6 | 2.2%p | [−5.05, −1.16] | 0.00 |
| random_topk@paper_costs | −19.9% | 26.3% | −0.71 | −0.95 | −33.7% | −13.1%p | 0.99 | −1.86 | 46.8 | 50.1 | 10.0%p | [−2.20, 0.86] | 0.02 |
| index:KOSPI (참고) | +10.0% | 21.9% | 0.55 | 0.74 | −20.7% | | | | | | | | |
| index:KOSDAQ (참고) | −8.1% | 27.2% | −0.17 | −0.23 | −27.1% | | | | | | | | |

- 풀링 EqualWeight(−6.9%)는 KOSDAQ 지수(−8.1%)에 가깝고 KOSPI(+10%)와는 크게 다르다(종목 수 기준 KOSDAQ 비중이 크고 동일가중이라 소형주 성격). 벤치마크 선택이 상대 지표를 크게 바꾼다 → 질문 1.
- random_topk의 비용 드래그 10%p는 연 턴오버 46.8(매주 거의 전량 교체) × 0.25%의 결과다.

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (9개)
  - 시그널 = 라벨이면 IC = 1, −라벨이면 −1, 난수면 평균 0 근처(t < 2.5); 적중률 1; 캘리브레이션(std = \|오차\|면 Spearman > 0.99, std 상수면 정의 불가)
  - 분위 수익률이 단조 증가·스프레드 t > 10, 레짐 라벨(반기, up/down)과 레짐별 IC 집계, 표본 부족 날짜 제외
  - 합성 수익률에서 CAGR·변동성·Sharpe·Sortino·MDD·Calmar·총수익이 quantstats(periods 252)와 상대 1e-6 ~ 1e-9 안에서 일치; MDD의 peak·trough·recovery
  - 상대 지표·거래 지표 손계산 일치(beta, alpha, TE, IR, AER, 자기 자신 대비 beta 1·AER 0; 연 턴오버, 체결 수, 교체 종목 수)
  - 라벨이 signal_date 다음날 시가에서 시작: as_of 이전 가격을 바꿔도 불변, 체결일 시가를 바꾸면 변함, 창 안 분할 무영향, 달력 밖 창 제외
  - deflated_sharpe가 n_trials 증가에 단조 감소, n_trials 1이면 SR* = 0
  - 같은 seed 부트스트랩 동일·다른 seed 상이, 구간이 점추정을 포함, 짧은 시계열은 NaN
  - trials.csv가 (run_id, engine, strategy, config_hash) 중복을 한 번만 센다
  - 실제 산출물: 오라클 IC > 0.6·적중률 > 0.75·스프레드 t > 5, 더미 \|IC\| < 0.03·적중률 0.5 ± 0.05, EW의 AER 0·beta 1, Sharpe가 CI 안
- [x] fake_oracle과 fake_dummy의 지표가 생성되고, 오라클 IC가 1에 가깝고(0.945) 더미 IC가 0에 가깝다(0.001)

## [확인 필요] 항목의 probe 결과

Stage 5 본문에 `[확인 필요]` 표시는 없다. 사용자 확인 그림은 scripts/probes/stage5_figures.py.

## 명세서와 달라진 점과 이유

1. **벤치마크 처리(D-6)**: `evaluate.benchmark: equal_weight`일 때 상대 지표의 벤치마크는 같은 run_id·엔진·비용 시나리오의 equal_weight 결과다. 지수는 `index:KOSPI`·`index:KOSDAQ` 행으로 절대 지표만 함께 둔다(상대 지표 열은 비어 있음). 지수를 주 벤치마크로 쓰려면 `evaluate.benchmark: KOSPI`.
2. **비용 전후 CAGR**: spec의 "--no-costs 실행과 비교"를 같은 전략의 `@no_costs` 폴더와 짝지어 `cagr_before_costs`, `cost_drag_cagr`로 넣었다. 쌍이 없으면 NaN.
3. **Deflated Sharpe의 n_trials**: 그 run_id의 trials.csv 행 수(엔진·전략·설정 해시가 다른 시도 수)를 쓴다. 현재 6(전략 3 × 비용 2). 다른 run_id의 시도를 합산할지는 Stage 8 리포트에서 정한다(질문 3). sr_var는 trials.csv의 Sharpe 분산을 쓰지 않고 공식 기본값 (1 + SR²/2)/T를 쓴다(시도 수가 적음).
4. **레짐 "상승·하락"의 정의**: 벤치마크의 라벨 구간(f → f+H) 수익률 부호. equal_weight면 그날 전 종목 라벨 평균. 월별이 아닌 리밸런싱 구간 단위다.
5. **라벨 창이 달력을 넘는 날짜는 제외**: 2025-06-30 시그널(f+5 = 2025-07-08)까지 라벨이 있어 49일 전부 채점된다(가격이 07-15까지).
6. **quantstats는 테스트 전용 의존성**: requirements.txt(D-8)에 넣지 않고 설치 안 되면 test_stage5_metrics.py 전체가 skip된다. pyproject optional `viz`에 이미 있다. 이 환경에는 0.0.86을 설치했다(yfinance 등 부속 패키지 포함).
7. **portfolio_metrics.csv의 행 = E 결과 폴더**(비용 접미사 포함, 예 `random_topk@paper_costs`). `strategy_base`·`costs` 열로 분리해 두었다.
8. 평가 결과 폴더는 run_id당 하나(data/F_metrics/{run_id}/)이며 엔진이 바뀌면 portfolio_metrics.csv가 덮어써진다(엔진 열로 구분, trials.csv는 누적). v2가 나오는 Stage 7에서 엔진별 파일로 나눌지 정한다.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **IC 요약표**(위)와 **분위별 수익률 막대그래프**: [figures/stage5_quantile_returns.png](figures/stage5_quantile_returns.png) — 오라클은 Q1 −9.0% → Q5 +10.8%로 단조, 더미는 ±0.1% 노이즈.
2. **벤치마크 전략 성과표**(위)와 누적 NAV: [figures/stage5_benchmarks_nav.png](figures/stage5_benchmarks_nav.png) — EqualWeight(풀링)가 KOSDAQ 지수와 거의 겹치고 KOSPI와 벌어진다.
3. data/F_metrics/fake_dummy_base/portfolio_metrics.csv 전체 열(Sortino, Calmar, MDD 날짜·회복일, 월별 분포, alpha, TE, 하루 교체 종목 수 등).

## 질문과 미결정 사항

1. **주 벤치마크 확정 (D-6)**: 잠정 equal_weight(같은 종목 집합)를 그대로 쓸지. 풀링 EW는 KOSDAQ 지수에 가깝다. 논문 재현(TopK, paper 프로필)의 AER·IR 기준 벤치마크를 코스피 지수로 할지도 함께(부록 C 결정 사항).
2. **거래소별 분해**: data_pipeline.md §9의 거래소별 IC(거래소 안에서 횡단면)·성과 분해를 F_evaluate에 넣을지. 지금은 풀링만.
3. **Deflated Sharpe의 시도 수 범위**: run_id별 trials.csv(현재)인지, 모든 run_id·K 변형(--set)까지 합친 전역 시도 수인지. Stage 8 compare에서 결정.
4. **backtest.delist_policy**: 여전히 null(제안 last_close). 모든 E 실행에 `--set`을 쓰고 있다.
5. docs/outline.md 수정분과 논문 PDF는 커밋하지 않았다.

## 후속 (2026-10-03, 사용자 지시: 벤치마크·거래소별 분해·DSR 범위) — D-16, D-17

**수행한 것**

1. **벤치마크(D-16)**: 주 벤치마크는 `evaluate.benchmark: equal_weight` 유지. 논문 재현용 지수 벤치마크 `evaluate.paper_benchmark: KOSPI`를 추가해 portfolio_metrics.csv에 `aer_vs_index`, `ir_vs_index`, `beta_vs_index`, `alpha_vs_index`, `tracking_error_vs_index`, `monthly_hit_ratio_vs_index`, `paper_benchmark` 열이 함께 나온다. fake_dummy_base 예: equal_weight AER 0 / IR — (자기 자신) vs KOSPI 대비 AER −16.9%p, IR −1.36, beta 1.02; random_topk KOSPI 대비 AER −30.0%p, IR −2.13.
2. **거래소별 분해(D-16, `evaluate.by_market: true`)**:
   - IC를 거래소 안에서 횡단면으로 계산한 `ic_by_market`(signal_metrics.json, ic_timeseries.csv의 `ic_<col>@<market>` 열). 오라클 exp_ret KOSPI 0.943 / KOSDAQ 0.943, 더미 0.003 / 0.011; naive mom20 KOSPI −0.040 / KOSDAQ −0.059, rev5 +0.031 / +0.036 (단기 반전은 KOSDAQ이 조금 더 강하다).
   - 전략 성과 기여도 portfolio_by_market.csv(전략 × 거래소: 평균 비중, 누적·연율 기여, 서브북 CAGR, 평균 종목 수). fake_dummy_base·논문 비용: EqualWeight는 KOSDAQ 비중 58%에서 연 −5.4%p 기여(서브북 CAGR −13.5%), KOSPI 42%에서 +4.7%p(+10.0%) — 풀링 EW의 부진이 KOSDAQ 쪽에서 왔다. momentum20_topk는 KOSDAQ 72%·KOSPI 28% 모두 서브북 CAGR −50% 이하. random_topk는 KOSPI 서브북 +33%, KOSDAQ −27%.
   - 근사: 전일 종가 비중 × 당일 수정 종가 수익률의 합이라 체결일 두 조각과 비용은 거래소별로 나누지 않는다(합계는 NAV 수익률과 거의 같다).
3. **DSR 시도 수(D-17, 에이전트 판단)**: 전역 장부 data/F_metrics/trials.csv(run_id, profile, fake, engine, strategy, config_hash, sharpe, cagr)를 두고, n_trials = 같은 프로필의 실제(비가짜) run_id 시도 수. 가짜 run_id는 자기 행만 센다(현재 각 6). 근거는 decisions.md D-17. `evaluate.dsr_scope: all`이면 프로필 구분 없이 센다.
4. **D-5 적용 뒤 재생성**: 가짜 예측 4종을 liq5 유니버스로 다시 만들었다(예측 파일 base 267 MB·219 MB, paper 1.3 GB·1.1 GB). 시그널 행은 base ∩ 예측이라 이전과 동일(base 42,008행, paper 215,406행)이고 `n_predicted_outside_universe`가 base 12,963·paper 67,394(liq5에만 있는 종목)로 기록된다. 전략·엔진·지표 수치는 변하지 않았다.
5. 테스트 3개 추가(거래소별 기여 손계산, n_trials 범위, 실제 산출물의 지수 벤치마크·거래소별 열). 전체 96 passed.

**남은 결정**: backtest.delist_policy(제안 last_close). 거래소별 **실행**(유니버스를 한 거래소로 제한한 run_id)은 넣지 않았다. 필요하면 `universe.indices`를 바꿔 별도 run_id로 돌린다.

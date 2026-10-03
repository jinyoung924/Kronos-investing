# Stage 2 보고서

2026-10-03. 범위: docs/decisions.md D-3·D-10·D-11·D-12 반영 + docs/spec.md "Stage 2. B 가짜 예측 + C_signal — 시그널". D-5는 구현하지 않았다. 다음 Stage는 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

**결정 기록 반영**

| 결정 | 반영 |
| --- | --- |
| D-3 | `B_model_infer/build_batch.py`의 자격 판정을 `qualify_window`·`eligible_tickers`로 분리. 더미·오라클 모두 `run_inference.run`을 거쳐 같은 판정을 쓴다. `C_signal/run_signal.py`가 날짜별로 유니버스 ∩ 예측 종목만 시그널 행으로 만들고 baseline features도 그 행에만 붙인다. signals/meta.json에 날짜별 n_universe, n_predicted, n_signal, n_universe_without_prediction, n_predicted_outside_universe, skip_reasons를 기록하고 `limitation` 문구를 넣었다. configs `signal:` 주석에 D-3 표기 |
| D-10 | `B_model_infer/make_fake_predictions.py`의 OracleBackend: 스텝 h 가격 = 원주가(t_h) × adj(t_h)/adj(as_of) (= F(t_h)/F(as_of)), 미래 정지일은 종가로 OHL 채우고 거래량 0, 가격 행이 없는 날은 마지막 유효 종가 이어 붙이고 거래량 0, 곱셈 노이즈 exp(N(0, 1e-4)), seed = project.seed. 채운 (as_of, ticker) 수를 manifest `oracle_fill_by_date`·`oracle_fill_total`에 기록 |
| D-11 | `signal.n_samples: 20` 추가. `C_signal/aggregate.select_samples`가 (as_of, ticker)마다 sample_id 오름차순 앞 n_samples개만 쓰고 부족하면 오류. run_signal은 manifest의 sample_count < n_samples면 시작 전에 오류 |
| D-12 | 가짜 예측·시그널은 default 프로필(base)만 생성했다. `--profile`은 spec 작업 1·2에 있어 플래그만 추가(기본 infer.default_profile). `universe.top_n_mktcap` null 유지, 주석 표기 |

**B_model_infer**

| 파일 | 설명 |
| --- | --- |
| build_batch.py | `qualify_window(hist, lookback, as_of, max_stale_days)` (옛 `_window`), `eligible_tickers(prices, tickers, as_of, lookback, max_stale_days)` 공개. build_batch 동작 불변 (기존 테스트 통과) |
| run_inference.py | `--profile` 플래그, `run(..., profile=None)`, `make_backend(..., profile)`. manifest에 profile과 lookback·pred_len·step·T·top_p·sample_count 기록(Stage 0부터 있던 것) |
| make_fake_predictions.py (신규) | `--kind dummy|oracle [--profile] [--run-id] [--start --end]`. dummy = DummyBackend(σ 0.02, signal_strength 0), oracle = OracleBackend. `make_fake(cfg, kind, profile, run_id, root, prices, constituents)` 함수를 테스트가 쓴다. run_id 기본 fake_{kind}_{profile}. manifest에 kind, fake, leaky, note, sigma/eps |

**C_signal**

| 파일 | 설명 |
| --- | --- |
| aggregate.py | `aggregate_predictions(preds, last_close, horizon, n_samples)`: exp_ret(H스텝 샘플 평균/last_close − 1), exp_ret_mean(샘플·스텝 1..H 평균), std(H스텝, ddof 0), p_up, pred_range(샘플·스텝 평균 (high − low)/last_close), n_samples, last_close. `select_samples`. validate_signals 통과 |
| baseline_features.py | `baseline_features(prices, adj_factor, as_of_dates, mom_window, vol_window, rev_window, ffill_limit)`: 수정 종가(raw × F)로 mom20 = c_t/c_{t−20} − 1, vol20 = 일간 단순수익률 20일 표준편차(ddof 1), rev5 = −(c_t/c_{t−5} − 1). 모두 후방 윈도우 |
| run_signal.py | `--run-id`. manifest(profile, pred_len=H, sample_count=N) → 예측 파일 전부 검증·결합 → A_prepared prices/adj_factor/universe → 유니버스 교집합(D-3) → last_close(as_of 종가, `data.price_basis`; raw) → 집계 + features → signals.parquet + meta.json(입력 53개 해시: manifest, prices, adj_factor, universe, as_of 파일 49개). 순수 핵심 `build_signals(...)`는 테스트가 직접 호출 |

**테스트·그림·문서**

| 파일 | 설명 |
| --- | --- |
| tests/test_stage2_signal.py | 12개 (아래). 합성 데이터(conftest, 분할·휴장 포함)에 가짜 예측을 tmp_path로 생성해 검증하고, 실제 생성물은 있을 때만 검사 |
| scripts/probes/stage2_figures.py | 사용자 확인용 그림 2장과 리밸런싱일 요약 |
| README.md, C_signal/__init__.py | 실행 명령·컬럼 설명 |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest -W error::FutureWarning          55 passed (Stage 1까지 43 + test_stage2_signal.py 12)
python -m B_model_infer.make_fake_predictions --kind dummy    fake_dummy_base : 49 as_of 파일 (2024-07-01 ~ 2025-06-30, 주간), 날짜당 785~1,028종목 × 5스텝 × 20샘플, 약 5초
python -m B_model_infer.make_fake_predictions --kind oracle   fake_oracle_base: 같은 49 파일. 채움: 정지일 95셀(49종목·날짜), 가격 행 없음 0
python -m C_signal.run_signal --run-id fake_dummy_base        42,008행, 49일, 종목/일 785~1,028, 제외(유니버스에 예측 없음) 2,774 (5.2초)
python -m C_signal.run_signal --run-id fake_oracle_base       동일 행 수·종목 집합
python scripts/probes/stage2_figures.py                       아래 "사용자 확인"
```

주요 수치

| 항목 | 값 |
| --- | --- |
| 리밸런싱일 | 49개, 2024-07-01, 07-08, 07-15 … 2025-06-16, 06-23, 06-30 (5거래일 간격) |
| 유니버스 / 시그널 행 / 제외 (일별 최소 ~ 최대) | 847 ~ 1,093 / 785 ~ 1,028 / 47 ~ 65 (평균 6.2% 제외, 사유 전부 nan_in_window) |
| 오라클 exp_ret vs 실현 5거래일 수정 종가 수익률 | n 42,008, 상관 1.000000, 최대 \|차이\| 1.2e-4, 평균 1.8e-5 (노이즈 ε 1e-4 범위) |
| 더미 시그널 | exp_ret 평균 +0.001, 표준편차 0.010, p_up 평균 0.499 (정보 없음 확인) |
| 오라클 시그널 | exp_ret 표준편차 0.094, std 컬럼 평균 9.6e-5 (> 0), mom20·vol20 NaN 0 |

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (12개)
  - 오라클 exp_ret ≈ 실제 H일 뒤 수정 종가 수익률, exp_ret_mean ≈ 1..H일 평균 수익률 (허용 6ε), std > 0, 미래 분할이 가짜 급락으로 나타나지 않음
  - 오라클이 as_of 2거래일 뒤 강제 2:1 분할을 as_of 원주가 스케일로 이어 붙임 (D-10), 미래 정지일 OHL = 종가·거래량 0, 상장폐지 뒤 마지막 종가 이어 붙임·manifest 집계
  - 더미·오라클의 예측 종목 집합 == `eligible_tickers` == `build_batch` 결과, manifest skipped 수 일치 (D-3)
  - paper형 프로필 메커니즘: as_of 매 거래일, horizon_step 1..10, 샘플 10 (합성 데이터 tmp_path, D-12에 따라 산출물로 만들지 않음)
  - 시그널 (as_of, ticker) 중복 없음, 그날 유니버스 밖 종목 없음, 시그널 종목 == 유니버스 ∩ 예측, meta 날짜별 수 합계 일치
  - p_up 0~1, std·pred_range ≥ 0, n_samples == 설정값
  - as_of 이후 가격·F를 바꿔도 baseline_features 불변, 정의 손계산 일치
  - aggregate가 앞 n_samples개만 사용(비연속 sample_id 포함), 부족 시 오류, last_close 누락 시 오류 (D-11)
  - 모델 스키마 예측(DummyBackend → predictions_from_array, Kronos 출력과 동일 스키마)이 aggregate에서 오류 없이 돈다
  - last_close가 price_basis(raw / adjusted)를 따른다
  - 실제 생성물: 두 run_id의 signals.parquet와 meta(H 5, N 20, n_samples 20, 49일) 검사, 오라클 상관 > 0.9999
- [x] (D-12 범위) fake_dummy_base, fake_oracle_base 두 run_id의 signals.parquet 생성. **spec의 fake_*_paper 두 개는 D-12에 따라 만들지 않았다** (아래 "달라진 점" 1)

## [확인 필요] 항목의 probe 결과

Stage 2 본문에는 `[확인 필요]` 표시가 없다. 사용자 확인 항목은 scripts/probes/stage2_figures.py로 만들었다(아래).

## 명세서와 달라진 점과 이유

1. **paper 프로필 가짜 예측을 만들지 않았다.** spec 완료 기준은 fake_{dummy,oracle}_{base,paper} 네 run_id지만 D-12가 "default 프로필(base)만 쓰고 paper 프로필 실행을 추가하지 않는다"고 정했다. 또 D-11의 `signal.n_samples: 20`은 paper 프로필(sample_count 10)의 시그널 계산을 막는다(아래 "질문" 1). 메커니즘(`--profile`, 매 거래일 as_of, H 10, N 10)은 spec 작업 1·2대로 구현하고 합성 데이터 단위 테스트로만 확인했다.
2. **dummy의 정의**: spec은 "각 샘플·스텝을 last_close × exp(N(0, σ))"이지만 D-3이 DummyBackend 경로를 지정했고 부록 A도 허용한다. DummyBackend는 누적 랜덤워크(스텝마다 분산이 커짐)다. 정보가 없다는 성질은 같다. σ = 0.02는 `SIGMA_DUMMY` 모듈 상수(테스트 상수, spec 지시)다.
3. **오라클의 F 출처**: D-10의 F(t_h)/F(as_of)를 수집 파일의 `adj_factor` 비율로 계산했다(adj_t/adj_s ≡ F_t/F_s, Stage 1 테스트). B_model_infer가 run_inference와 같은 가격 프레임(data/raw) 하나만 읽게 하기 위해서다. A_prepared를 B가 읽지 않는다.
4. **오라클의 "가격 행 없음" 집계 이름**: manifest 키는 `no_bar_fill_*`다(실데이터에서는 상장폐지만 해당, Stage 1 probe). 실제 49일에서는 0건이었다. 유니버스 필터가 유동성·정지 기준으로 폐지 전에 종목을 이미 빼기 때문이다.
5. **시그널 컬럼에 last_close 포함**: spec의 6개 외에 last_close를 넣었다(스키마의 선택 컬럼). Stage 5·6에서 가격 기준 확인에 쓴다.
6. **vol20 정의**: "일간 수익률 20일 표준편차"를 단순수익률·ddof 1·연율화 없음으로 구현했다(옛 코드는 로그수익률 연율화). 전략은 순위만 쓰므로 영향 없다. pct_change는 NaN 패딩 없이 계산한다.
7. **pred_range의 스텝 범위**: 개요서 표가 스텝을 명시하지 않아 옛 구현과 같이 모든 스텝·샘플 평균으로 했다.
8. **run_signal의 입력 해시**: as_of 파일 49개를 모두 meta.json inputs에 넣었다(파일당 sha256). paper(매일)면 250개 정도가 된다.
9. **"기존 Kronos 예측 파일" 테스트**: 레포에 실제 Kronos 예측이 아직 없어(Stage 6·부록 C) 같은 스키마의 DummyBackend 출력으로 대신했다.
10. **universe.markets 등 다른 키는 읽지 않았다**(D-2). run_signal·make_fake_predictions가 새로 읽는 설정 키: project.seed, period.*, infer.*, universe.variant·max_stale_days(기존 B 경로), data.price_basis, data.ffill_limit, signal.*.
11. 수집 코드·데이터 경로는 건드리지 않았다. 생성물은 data/B_predictions/fake_*_base, data/C_signals/fake_*_base뿐이다(gitignore).

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **리밸런싱일 목록**: 49개, 처음 2024-07-01 / 07-08 / 07-15, 마지막 2025-06-16 / 06-23 / 06-30. B_model_infer와 같은 `common.calendar.rebalance_dates`로 만든다.
2. **날짜별 시그널 종목 수**: [figures/stage2_signal_counts.png](figures/stage2_signal_counts.png) — 유니버스(파랑)와 시그널 행(주황)의 간격이 제외 종목(초록, 47~65)이다. 모든 전략이 주황 집합에서 고른다(D-3).
3. **오라클 산점도**: [figures/stage2_oracle_scatter.png](figures/stage2_oracle_scatter.png) — exp_ret와 실현 5거래일 수정 종가 수익률이 대각선 위에 있다(상관 1.000000). 이 오라클은 Stage 4 엔진 배선 테스트 전용이며 전략 성과로 해석하지 않는다.
4. D-3 한계 문구(signals meta `limitation`)가 F_evaluate·G_report 보고서에 그대로 들어갈 예정이다. 문구 확인: "lookback 안에 거래정지 이력이 있는 종목은 모든 전략에서 제외된다."

## 질문과 미결정 사항

1. **D-11과 paper 프로필의 충돌**: `signal.n_samples: 20`인데 paper 프로필은 sample_count 10이라 run_signal이 D-11대로 오류를 낸다. Stage 6에서 kronos_paper_v1의 시그널을 만들려면 프로필별 값이 필요하다. 선택지: (a) `infer.profiles.<p>.n_signal_samples`로 옮긴다, (b) `signal.n_samples`를 {base: 20, paper: 10} 매핑으로 바꾼다, (c) paper도 sample_count를 20 이상으로 뽑는다(부록 C 선택 2, GPU 시간 2배). Stage 6 전에 결정이 필요하다.
2. **D-12 해석**: "paper 프로필 실행을 추가하지 않는다"를 "paper 가짜 예측 산출물도 만들지 않는다"로 읽었다. spec 완료 기준(네 run_id)을 원하면 `make_fake_predictions --kind dummy --profile paper`로 즉시 만들 수 있지만 1번 때문에 시그널은 만들 수 없다.
3. **dummy 정의**: spec의 i.i.d. 정의가 꼭 필요하면 OracleBackend처럼 간단한 백엔드를 추가할 수 있다. 현재는 DummyBackend(D-3 지시)다.
4. 커밋에 docs/decisions.md(사용자가 추가한 D-10~D-13, 내용 변경 없음)를 포함했다. docs/outline.md 수정분과 논문 PDF는 여전히 커밋하지 않았다.

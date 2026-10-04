# Stage 6 보고서

2026-10-04. 범위: docs/spec.md "Stage 6. Kronos 전략과 실제 예측 연결". 다음 Stage는 시작하지 않았다.

**요약**: Kronos 전략 3개와 예측 검증 모듈을 구현했고, 실제 예측 `kronos_base_v1`로 C → D → E(v1) → F를 끝까지 돌렸다. `kronos_paper_v1`은 아직 추론하지 않아 TopK는 가짜 예측(fake\_dummy\_paper, fake\_oracle\_paper)으로 배선만 확인했다. 실제 예측을 처음 본 결과, **Kronos의 exp\_ret은 "입력 400일 구간의 평균으로 되돌아간다"는 예측과 거의 같고(Spearman −0.94), IC는 0.02(t 0.9)로 유의하지 않다.**

## 구현한 것 (파일 목록과 한 줄 설명)

| 파일 | 설명 |
| --- | --- |
| D\_strategy/topk.py | 논문 재현 TopK. Qlib TopkDropoutStrategy 규칙(k, n\_drop, hold\_min\_days, 매도 bottom·매수 top). 상태 = 보유 종목별 보유 일수. 매수 1/k, 유지 NaN(보류), 매도는 미포함. 동점은 ticker 순 |
| D\_strategy/conf\_weighted.py | score = exp\_ret / std. score ≥ threshold이고 score > 0인 종목을 score 비례 가중. 없으면 전액 현금. std가 0·음수·NaN이면 대상 아님. threshold null이면 오류 |
| D\_strategy/vol\_target.py | exp\_ret 상위 K를 1 / pred\_range 비례 가중. pred\_range가 0·음수·NaN인 종목은 대상에서 빼고 남은 종목 중 K개를 고른다 |
| B\_model\_infer/validate\_predictions.py | 실제 예측 폴더 검사 → `data/B_predictions/{run_id}/validation.json`. manifest와 프로필 설정 대조, 날짜 목록 대조, 날짜별 유니버스 겹침, 스텝·샘플 수, 가격 기준(원주가/수정주가) |
| D\_strategy/run\_strategy.py | `--strategy all` = 그 run\_id 프로필과 같은 프로필의 전략 전부(이름을 직접 준 전략의 프로필 불일치는 여전히 오류) |
| common/schema.py | 시그널 검증에서 `pred_range ≥ 0` 조건 제거 (아래 "달라진 점" 1) |
| common/paths.py | `validation_file(run_id)` |
| scripts/probes/stage6\_mean\_reversion.py | exp\_ret과 입력 구간 z의 관계, 스텝별 회귀 |
| scripts/probes/stage6\_figures.py | 그림 4개 |
| tests/test\_topk\_dropout.py (5개), tests/test\_stage6\_kronos.py (6개) | 아래 |
| tests/test\_stage3\_strategy.py | 새 전략 3개의 테스트용 파라미터 추가, 보류(NaN)가 있는 비중 파일 허용 |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest                                                        111 passed (Stage 5·부록 C까지 97 + test_topk_dropout 5 + test_stage6_kronos 6 + Stage 3 공통 계약 3)
python -m B_model_infer.validate_predictions --run-id kronos_base_v1          ok: true
python -m C_signal.run_signal --run-id kronos_base_v1                         42,008행, 49일, 날짜당 785~1,028종목
python -m D_strategy.run_strategy --run-id kronos_base_v1 --strategy equal_weight,momentum20_topk,random_topk,conf_weighted,vol_target \
       --set strategies.conf_weighted.threshold=0.5
python -m E_backtest.run_backtest --run-id kronos_base_v1 --strategy all --costs paper --set backtest.delist_policy=last_close
python -m E_backtest.run_backtest --run-id kronos_base_v1 --strategy all --no-costs    --set backtest.delist_policy=last_close
python -m F_evaluate.run_evaluate --run-id kronos_base_v1 --strategy all --set strategies.conf_weighted.threshold=0.5 --set backtest.delist_policy=last_close
python scripts/probes/stage6_mean_reversion.py ; python scripts/probes/stage6_figures.py
# TopK 배선 확인 (가짜 예측, 벤치마크 3개는 --set strategies.<name>.profile=paper)
python -m D_strategy.run_strategy --run-id fake_{dummy,oracle}_paper --strategy topk,equal_weight,momentum20_topk,random_topk ...
```

### validate\_predictions 요약 (kronos\_base\_v1)

| 항목 | 결과 |
| --- | --- |
| manifest vs `infer.profiles.base` | 일치 (lookback 400, pred\_len 5, step 5, sample\_count 20, T 1.0, top\_p 0.9) |
| as\_of 날짜 | 49 / 49, 빠진 날짜·남는 날짜 없음 |
| 스텝·샘플 수 | 모든 종목 horizon\_step 1..5, 샘플 20개 |
| 추론 유니버스(liq5) 대비 | 날짜당 예측 1,040~1,326종목, 유니버스 1,123~1,414종목. 예측 없는 종목 날짜당 68~88개(합 3,828 = manifest의 `nan_in_window`). 유니버스 밖 예측 0 |
| 백테스트 유니버스(base) 대비 | 날짜당 847~1,093종목 중 예측 없는 종목 47~65개(합 2,774, 6.2%) → 시그널에서 빠진다 |
| 가격 기준 | 아래 probe 절 |

예측이 없는 종목은 전부 `nan_in_window`(입력 400일 구간에 결측)다. 가짜 예측 때도 같은 수(3,828)였으므로 실제 추론에서 새로 생긴 손실이 아니라 build\_batch의 기존 규칙이다. 백테스트 유니버스의 6%가 매번 빠지는 점은 질문 4에 남긴다.

### 시그널 평가 (kronos\_base\_v1, H 5, 49일)

| 시그널 | IC 평균 | IC 표준편차 | ICIR | t | 양수 비율 |
| --- | --- | --- | --- | --- | --- |
| Kronos exp\_ret | +0.020 | 0.156 | 0.13 | 0.88 | 59% |
| Kronos exp\_ret\_mean | +0.022 | 0.152 | 0.14 | 1.00 | 53% |
| (naive) rev5 | +0.034 | 0.125 | 0.27 | 1.88 | 59% |
| (naive) mom20 | −0.051 | 0.133 | −0.39 | −2.70 | 37% |

- **Kronos의 IC는 0과 구별되지 않고, 5일 반전(rev5)보다 낮다.**
- 분위별 평균 실현수익률: Q1 −0.19%, Q2 −0.05%, Q3 −0.03%, Q4 −0.05%, Q5 +0.09%. Q5−Q1 스프레드 +0.28%p (t 0.58).
- 적중률(p\_up > 0.5 vs 실현 부호) 50.9%. 캘리브레이션(std vs |오차| Spearman) +0.45: 불확실성 추정은 실제 오차 크기와 맞는다.
- 거래소별: KOSDAQ exp\_ret IC +0.044 (t 2.16), KOSPI +0.002 (t 0.07).
- 레짐별: 상승 구간 +0.099 (t 3.5, 26일), 하락 구간 −0.070 (t −2.8, 23일). 반기별로는 +0.021, +0.018로 비슷하다.

### 전략 성과 (kronos\_base\_v1, 엔진 v1, 벤치마크 = equal\_weight 같은 비용)

| 전략 | 비용 | CAGR | 변동성 | Sharpe | MDD | AER | IR | KOSPI 대비 AER | 연 턴오버 | 평균 보유 | Sharpe 95% CI | DSR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| conf\_weighted | 없음 | −0.8% | 27.6% | 0.11 | −25.7% | +5.0%p | 0.72 | −10.8%p | 14.1 | 421 | [−1.34, 1.79] | 0.07 |
| conf\_weighted | 논문 | −4.2% | 27.6% | −0.02 | −26.7% | +2.7%p | 0.43 | −14.2%p | 14.1 | 421 | [−1.46, 1.65] | 0.06 |
| vol\_target | 없음 | −6.3% | 34.9% | −0.01 | −29.8% | −0.5%p | 0.14 | −16.3%p | 17.0 | 52 | [−1.40, 1.54] | 0.06 |
| vol\_target | 논문 | −10.2% | 35.0% | −0.13 | −31.0% | −3.3%p | −0.04 | −20.2%p | 17.0 | 52 | [−1.52, 1.40] | 0.04 |
| equal\_weight | 논문 | −6.9% | 25.3% | −0.15 | −26.2% | 0 | — | −16.9%p | 4.9 | 857 | [−1.66, 1.54] | 0.04 |
| momentum20\_topk | 논문 | −65.3% | 33.5% | −2.98 | −67.0% | −58.4%p | −3.92 | −75.3%p | 24.7 | 51 | [−5.05, −1.16] | 0.00 |
| random\_topk | 논문 | −19.9% | 26.3% | −0.71 | −33.6% | −13.1%p | −1.86 | −30.0%p | 46.8 | 50 | [−2.20, 0.86] | 0.01 |
| index:KOSPI (참고) | | +10.0% | 21.9% | 0.55 | −20.7% | | | | | | | |
| index:KOSDAQ (참고) | | −8.1% | 27.2% | −0.17 | −27.1% | | | | | | | |

- conf\_weighted는 같은 종목 집합의 동일가중을 비용 후 +2.7%p 앞서지만 Sharpe 신뢰구간이 0을 넓게 포함하고 DSR 0.06이다. **우연과 구별되지 않는다.**
- vol\_target은 동일가중보다 낮다. 변동성 35%로 "저변동 가중"인데도 가장 높다(상위 50개가 급락 종목에 몰린다, 아래 probe).
- 벤치마크 3개의 수치는 fake\_dummy\_base 때와 같다(시그널 종목 집합이 같으므로 예상대로다).
- conf\_weighted는 날짜당 204~629종목(평균 415), 최대 비중 2.3%. vol\_target 최대 비중 5.2%.
- 비용은 논문 비용만 돌렸다. `--costs kr`은 `costs.sell_tax_table`이 비어 있어 멈춘다.

### TopK 배선 확인 (가짜 예측, 성과 의미 없음)

| run\_id | 날짜 | 보유 수 | 하루 교체 평균 | 엔진 status | 비고 |
| --- | --- | --- | --- | --- | --- |
| fake\_dummy\_paper | 241 | 항상 50 | 4.0 | hold 10,990 / filled 1,686 / scaled 282 / no\_price 27 | CAGR −20.6% (정보 없음) |
| fake\_oracle\_paper | 241 | 항상 50 | 4.2 | hold 10,956 / filled 2,018 | NAV 3,504배 (미래를 아는 시그널, 배선 확인용) |

엔진의 hold 건수는 비중 파일의 NaN 행 수와 같다.

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (111개)
  - 세 전략이 Stage 3 공통 비중 제약 테스트에 자동 포함되어 통과
  - TopK 합성 30일: 보유 수 항상 k, 하루 매도·매수 ≤ n, 보유 일수 미달 종목 미매도, 첫날 이후 NaN 보류와 NaN 제외 합 ≤ 1, 시그널 불변이면 교체 0, 같은 입력 같은 출력, 동점 시 행 순서와 무관
  - TopK 비중을 v1에 넣으면 hold 건수 = 보류 종목 수, 첫날 이후 하루 거래 종목 수 ≤ 2n
  - conf\_weighted: 모든 score가 임계치 미만이면 전액 현금, std 0·NaN 종목 제외, threshold 없으면 오류
  - vol\_target: pred\_range 0·NaN 종목이 있어도 오류 없이 제외
  - validate\_predictions: 프로필 불일치, 빠진 날짜, 스텝·샘플 수 불량, 원주가/수정주가 판별
- [x] validate\_predictions 요약이 보고서에 있다 (불일치 없음)
- [ ] **부분 통과**: kronos\_base\_v1로 5개 전략의 v1 결과와 지표 생성 완료. **kronos\_paper\_v1은 추론하지 않아 TopK + 벤치마크 3개의 실제 결과가 없다.**
- [x] TopK 설정 대조표 (아래)

### TopK 설정 대조표 (spec 부록 D)

| 항목 | 논문 / 공식 레포 | 한국 적용 (configs `strategies.topk`, `infer.profiles.paper`) | 차이와 이유 |
| --- | --- | --- | --- |
| 입력·예측 길이 | lookback 90, H 10 | 90, 10 | 동일 |
| 샘플링 | T 0.6, top-p 0.9, N 10 | 0.6, 0.9, 10 | 동일 (논문 표 6) |
| 시그널 | 스텝 평균 예측 종가 / 현재가 − 1 | `exp_ret_mean` | 동일 |
| 순위 주기 | 매일 | schedule daily | 동일 |
| k, n | CSI 300: 50, 5 | k 50, n\_drop 5 | 동일 값. 유니버스는 KOSPI+KOSDAQ 풀링(하루 약 900종목)이라 CSI 300보다 3배 크다 (D-15, 질문 2) |
| 최소 보유 | 5일 | hold\_min\_days 5 | 동일 |
| 교체 규칙 | 보유 + 미보유 상위 합집합의 하위 n 매도, 미보유 상위 매수 | 같은 규칙 | 매수 수를 "보유 일수 조건을 통과한 매도 수"로 센다(spec 작업 1). Qlib은 조건 전 매도 수로 세어 보유 수가 k를 넘을 수 있다 |
| 매수 금액 | 매도 대금 + 현금을 매수 종목에 전부 배분 | 신규 매수 1/k 고정 | 차이 (부록 D와 같음) |
| 체결 | 다음날 시가 | 다음 거래일 시가 | 동일 |
| 비용 | 매수 0.10%, 매도 0.15% | `--costs paper` 같은 값. kr은 세율표 대기 | — |
| 벤치마크 | CSI 300 지수 | `evaluate.paper_benchmark: KOSPI` + equal\_weight | D-16 |

## [확인 필요] 항목의 probe 결과

**1. 예측의 가격 기준 (원주가 vs 수정주가)** — validate\_predictions

- as\_of에 원주가와 수정주가가 1% 넘게 다른 종목·날짜 1,959개 중 1,713개(87%)가 원주가에 더 가깝다. 스텝 1 예측 / 원주가 중앙값 1.012, / 수정주가 중앙값 1.128.
- 차이가 20% 넘는 경우(실제 분할급) 651개 중 **646개(99%)가 원주가**에 가깝다. 나머지 246개는 두 기준의 차이가 작아 아래 2번의 예측 변동에 묻힌 것이다.
- 결론: 예측은 as\_of 원주가 기준이고 `data.price_basis: raw`(D-1)와 일치한다.

**2. Kronos 시그널의 정체: 입력 구간 평균으로의 회귀** — scripts/probes/stage6\_mean\_reversion.py

실제 exp\_ret의 분포가 5일 수익률로는 비현실적이었다(1% 분위 −58%, 중앙값 +1.2%, 99% 분위 +91%, |exp\_ret| > 50%인 행이 7.4%). 원인을 추적했다.

| 입력 구간 안에서 마지막 종가의 z 분위 | z 중앙값 | exp\_ret 평균 | p\_up 평균 |
| --- | --- | --- | --- |
| Q1 (구간 저점 부근) | −1.58 | +38.7% | 0.99 |
| Q2 | −0.93 | +12.8% | 0.89 |
| Q3 | −0.26 | +2.8% | 0.61 |
| Q4 | +0.69 | −6.2% | 0.15 |
| Q5 (구간 고점 부근) | +2.15 | −28.6% | 0.00 |

- **Spearman(exp\_ret, z) = −0.936.** 순위로 보면 Kronos의 exp\_ret은 "400일 구간에서 현재가가 평균보다 얼마나 높은가"의 역순과 거의 같다.
- 스텝별로 보면 예측 z = 0.59 × 현재 z(1일 뒤) → 0.39 → 0.30 → 0.24 → 0.19(5일 뒤). 5일 만에 구간 평균까지 거리의 약 80%를 되돌아간다고 예측한다.
- **파이프라인 버그가 아니다.** 로컬 CPU에서 공식 `KronosPredictor.predict`를 같은 입력으로 직접 호출해 8종목을 비교했고, 우리 결과(GPU)와 같은 크기였다(예: z +5.9 종목 5일 뒤 공식 −60% / 우리 −59%). 토크나이저 encode → decode 왕복 오차는 z 0.03~0.06으로 작다. T를 0.6, 0.01로 낮춰도 같다.
- lookback을 90으로 줄이면(paper 프로필 길이) 회귀가 약해지지만 남는다(z +3.3 종목 5일 뒤 −32%).
- 음수 가격 예측도 여기서 나온다(pred\_close ≤ 0: 163행, 78 종목·날짜). 구간 평균이 현재가보다 한참 높고 표준편차가 큰 종목에서 표준화 공간의 잡음이 0 아래로 내려간다.
- 영향: conf\_weighted는 exp\_ret / std가 큰 종목, 즉 구간 저점 부근 종목을 사고, vol\_target의 상위 50개도 같은 쪽이다. 두 전략은 사실상 "400일 평균 대비 많이 빠진 종목을 사는" 전략이다.

## 명세서와 달라진 점과 이유

1. **`pred_range ≥ 0` 검증 제거 (common/schema.py)**: 실제 Kronos 샘플은 pred\_high ≥ pred\_low를 보장하지 않는다(550만 행 중 18,046행 0.33%, 종가가 [저가, 고가] 밖인 행 3.4%). 평균이 음수인 종목·날짜가 15개 있어 시그널 검증이 멈췄다. 정의(spec의 mean(high − low) / last\_close)는 그대로 두고 검증만 풀었다. 시그널 파일에서 pred\_range ≤ 0은 1행이고 vol\_target이 제외한다. 다른 Stage(common) 수정이다.
2. **`--strategy all`의 뜻**: 등록된 전략 전부가 아니라 run\_id 프로필과 같은 프로필의 전략 전부다. topk(paper)가 등록되면서 base run\_id에서 `all`이 항상 프로필 오류로 멈추게 되기 때문이다.
3. **TopK: 시그널에서 사라진 보유 종목은 보유 일수와 무관하게 팔린다.** 전략 출력은 그날 시그널에 있는 종목만 담을 수 있어(Stage 3 계약) 보류(NaN)로 표현할 수 없다. fake\_dummy\_paper에서 규칙 매도 811건, 이 강제 매도 153건이고, 그날은 매수 수가 n을 넘는다(241일 중 33일, 최대 7). spec의 "하루 매도·매수 ≤ n"은 유니버스 이탈이 없는 합성 데이터에서만 성립한다 → 질문 3.
4. **conf\_weighted에 score > 0 조건 추가**: 임계치가 0 이하일 때 음수 비중이 나오지 않게 한다. 비중 상한은 두지 않았다(spec에 없음).
5. **vol\_target의 pred\_range 0 처리**: 가중을 정의할 수 없으므로 후보에서 뺀 뒤 K개를 고른다(K개를 고른 뒤 빼지 않는다).
6. **conf\_weighted 임계치**: 설정은 null 그대로 두고 `--set strategies.conf_weighted.threshold=0.5`(이전 설정의 자리표시 값)로 돌렸다. 결과를 보기 전에 정한 값이다.
7. **paper run\_id의 벤치마크 전략**: 설정의 profile이 base라 `--set strategies.<name>.profile=paper`로 돌렸다. schedule은 weekly(H = 10거래일 간격) 그대로다.
8. **validate\_predictions의 가격 기준 판별**: spec의 "분할 이력이 있는 종목" 대신 "as\_of에 원주가와 수정주가가 다른 종목·날짜 전부"로 넓히고 차이 20% 초과를 따로 셌다.
9. `B_model_infer/verify_run.py`(부록 C에서 추가, Pod용 구조 검사)와 일부 겹친다. validate\_predictions는 로컬에서 설정·유니버스·가격까지 보는 쪽이다.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **exp\_ret vs 입력 구간 z 산점도**: [figures/stage6_exp_ret_vs_window_z.png](figures/stage6_exp_ret_vs_window_z.png). 이 Stage의 핵심 발견이다.
2. **분위별 실현수익률**: [figures/stage6_quantile_returns.png](figures/stage6_quantile_returns.png)
3. **누적 NAV**: [figures/stage6_nav.png](figures/stage6_nav.png) (전략 5개, 논문 비용, KOSPI·KOSDAQ 지수)
4. **TopK 하루 교체 종목 수 분포**(가짜 예측): [figures/stage6_topk_swaps.png](figures/stage6_topk_swaps.png)
5. data/F\_metrics/kronos\_base\_v1/ 의 signal\_metrics.json, portfolio\_metrics.csv, portfolio\_by\_market.csv
6. trials.csv: 이번 실행으로 kronos\_base\_v1 10행이 전역 장부에 들어갔고 DSR의 n\_trials는 10(base 프로필의 실제 run\_id 시도 수)이다. 임계치나 K를 바꿔 다시 돌리면 설정 해시가 달라 행이 늘어난다.
7. 삭제(샌드박스가 막음, 부록 C 후속에서 이월): `git rm B_model_infer/pod_bundle.py B_model_infer/pod/setup_pod.sh`, `rm -r data/pod_bundles`.

## 질문과 미결정 사항

1. **kronos\_paper\_v1 추론을 돌릴 것인가.** 약 7.3시간, $5.4(RunPod/README.md §6). 위 probe 2로 보면 lookback 90에서도 평균 회귀 성향이 남는다. 돌리면 TopK 실제 결과로 이 Stage의 남은 완료 기준이 채워진다.
2. **TopK의 유니버스 대응안**: (a) KOSPI 시총 상위 200 + k 50·n 5, (b) 풀링 전체 + k 200·n 10. 지금 설정은 풀링 전체 + k 50·n 5라 어느 쪽도 아니다. 추론은 liq5 전체로 하면 두 안 모두 재추론 없이 걸러 쓸 수 있다.
3. **TopK 보유 종목이 유니버스에서 빠진 날**: 지금은 강제 매도다. 유동성 필터 경계에서 드나드는 종목 때문에 교체가 늘어난다. 유지하려면 "시그널이 없는 보유 종목도 NaN으로 낼 수 있다"로 Stage 3 계약을 넓혀야 한다.
4. **`nan_in_window`로 백테스트 유니버스의 6%가 매번 빠진다.** 입력 400일에 결측이 하나라도 있으면 추론에서 제외하는 규칙 때문이다. 그대로 둘지, 거래정지일을 채워 넣을지.
5. **평균 회귀 성향을 어떻게 다룰 것인가.** 선택지: (a) 그대로 두고 "zero-shot Kronos는 이 시장에서 이렇게 동작한다"를 결과로 보고, (b) lookback을 줄인 프로필을 추가해 비교, (c) 시그널을 횡단면 순위나 z로 바꿔 크기 왜곡을 줄임. (b)·(c)는 결과를 본 뒤의 변경이라 trials.csv에 시도로 남는다. 에이전트는 (a)로 두고 Stage 7로 넘어가는 것을 권한다.
6. **conf\_weighted 임계치 확정**(지금 0.5를 `--set`으로), **`backtest.delist_policy`**(지금 last\_close를 `--set`으로), **`costs.sell_tax_table`**(비어 있어 `--costs kr` 불가). 셋 다 여전히 사용자 결정 대기다.
7. 이번 base 실행의 Pod 기록(`docs/stage_reports/pod_run.md`)은 아직 없다.

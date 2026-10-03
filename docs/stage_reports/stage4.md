# Stage 4 보고서

2026-10-03. 범위: docs/spec.md "Stage 4. E_backtest — 엔진 v1과 비용" + 사용자 지시(K는 전략 파라미터로 두고 실행 시 바꿔 테스트, 용어 `as_of_date` 통일 → docs/decisions.md D-14). 다음 Stage는 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

**사용자 지시 반영 (D-14)**

| 항목 | 반영 |
| --- | --- |
| K는 전략 파라미터 | configs `strategies.<name>.k`는 전략 파라미터(기본 20)로 유지하고 "[사용자 확정 필요]" 표기를 지웠다. `run_strategy`·`run_backtest`에 `--set KEY=VALUE`(반복 가능, YAML 파싱)를 추가해 `--set strategies.random_topk.k=50`처럼 실행 시 바꿔 테스트한다(`common.config.parse_set_overrides`). 바꿔 돌린 결과의 구분(run_id·trials.csv)은 Stage 5에서 |
| 용어 통일 | docs/spec.md Stage 3의 비중 파일 컬럼 표기 `rebalance_date` → `as_of_date` (1곳). 코드·스키마는 이미 as_of_date. decisions.md에 D-14 기록 |

**E_backtest**

| 파일 | 설명 |
| --- | --- |
| costs.py | `CostModel(cfg, scenario)`: kr(수수료 + 슬리피지 bp, 매도에 `costs.sell_tax_table`의 시장별·시행일별 세율; 표가 비면 ConfigError), paper(`costs.paper` 매수 0.10% 매도 0.15%, 세금·슬리피지 없음), none(0). `buy_cost_rate(date, market)`, `sell_cost_rate(date, market)`, 벡터 `rates(date, markets)`, `describe()` |
| engine_v1_weights.py | `run_v1(weights, prices, calendar, cost_model, cfg, end=None) → dict(nav, daily, trades, holdings, delisted, unfilled_signal_dates, delist_policy_used)`. 날짜 루프 × 종목 벡터. 시간 규칙 1~4 구현(다음 거래일 시가 체결, 종가 평가, 체결일 두 조각 수익률, drift, 수정가격). 보류(NaN)·시가 없음(no_price)·매수 비례 축소(scaled)·상장폐지(delist_policy, 필요할 때만 요구) 처리. 종료 시 `assert_fill_after_signal(trades)` |
| run_backtest.py | `--run-id --strategy a,b|all --engine v1 [--costs kr|paper] [--no-costs] [--set ...]`. `all` = D_weights 폴더의 파일(D_strategy를 import하지 않음). 출력 data/E_backtest/{run_id}/v1/{strategy}[@paper_costs|@no_costs]/: nav.csv(date, nav, ret, cost), daily.csv(+ gross_ret, cash, n_holdings, turnover, buy_scale), trades.parquet(fill_date, signal_date, ticker, w_pre, w_tgt, trade_w, cost, status), holdings.parquet(date, ticker, weight), delisted.csv(있을 때), meta.json(비용 모델 설명, 요약 통계, 상장폐지 포지션 목록, 미체결 시그널일) |
| tests/test_stage4_engine_v1.py | 13개 (아래) |
| scripts/probes/stage4_figures.py | 오라클 vs 1주 늦춤 vs EqualWeight 누적 NAV, RandomTopK seed 분포 |
| README.md, D_strategy/run_strategy.py, common/config.py | 실행 명령, `--set` |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest -W error::FutureWarning                                           79 passed (Stage 3까지 67 + test_stage4_engine_v1.py 13; 그중 실제 데이터 3)
python -m E_backtest.run_backtest --run-id fake_dummy_base --strategy all --no-costs   --set backtest.delist_policy=last_close
python -m E_backtest.run_backtest --run-id fake_dummy_base --strategy all --costs paper --set backtest.delist_policy=last_close
python -m E_backtest.run_backtest --run-id fake_dummy_base --strategy equal_weight      -> ConfigError: costs.sell_tax_table is empty (의도된 중단)
python scripts/probes/stage4_figures.py
```

| 전략 (fake_dummy_base, v1, 2024-07-01 ~ 2025-06-30) | NAV 끝 (비용 0) | NAV 끝 (논문 비용) | 누적 비용 (NAV 단위) | 체결 48회 평균 교체율 | 평균 보유 | trades status (비용 0) |
| --- | --- | --- | --- | --- | --- | --- |
| equal_weight | 0.9452 | 0.9344 | 0.0101 | 0.098 | 856.9 | filled 22,866 / scaled 20,618 / no_price 169 |
| momentum20_topk | 0.4356 | 0.4075 | 0.0419 | 0.545 | 20.0 | filled 1,385 / scaled 71 / no_price 8 |
| random_topk | 0.9355 | 0.8334 | 0.0946 | 0.960 | 20.1 | filled 1,796 / scaled 79 / no_price 4 |

- 마지막 시그널(2025-06-30)은 체결일이 period.end 뒤라 미체결로 기록된다(`unfilled_signal_dates`). 체결 48회.
- **momentum20_topk −56%**: 엔진 밖에서 같은 종목의 시가→시가 주간 수익률을 단순 체인한 값 0.40과 일치한다(정지 종목 무시한 근사). 20일 급등 상위 20종목을 매주 사는 전략이 한국 시장의 단기 반전에 크게 당하는 것이지 엔진 오류가 아니다. 최악 주 −18%(2024-08-20, 2024-12-11).
- **오라클 배선**: exp_ret 상위 20(5거래일 실제 수익률을 아는 전략) 비용 0 총수익 +41,350,388%(NAV 413,505), 같은 종목을 1주 늦게 체결하면 −46%, EqualWeight −5.5%. 미래 정보가 하루라도 어긋나면 우위가 사라진다(정보가 묻은 종목은 그 뒤 반전까지 한다).
- **랜덤 대조군**: RandomTopK(k 20) 16 seed 총수익 평균 −8.3%(표준편차 12.0%, −25.6% ~ +10.9%) vs EqualWeight −5.5%. 20종목 포트폴리오의 분산이 크지만 평균은 EW 근처로, 엔진·유니버스에 체계적 편향이 없다.
- **비용 영향(논문 비용)**: EW −1.1%p, Momentum −2.8%p, Random −10.2%p(교체율 0.96 × 매수 0.10% + 매도 0.15% ≈ 주당 0.24% × 48회).
- **상장폐지 보유 포지션**(EqualWeight만): 다나와 119860(2024-09-23 마지막 봉, 비중 0.12%), 제이시스메디칼 287410(2024-11-06, 0.13%). 둘 다 공개매수 뒤 자진 상장폐지로, 정지 중 매도 신호(목표 0)가 체결되지 못해 남은 잔여 포지션이다. `last_close`로 현금화됐다.

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (13개)
  - 장난감 예제(3종목·6거래일, 비용 매수 1%·매도 2%): 체결일 두 조각 수익률·비용 차감·drift·두 번째 리밸런싱(매도·매수·축소 없음)까지 손계산을 테스트 안에 단계별로 적고 엔진 NAV와 1e-10 안에서 일치, 체결일 비용·trades status·holdings 합 = 1 − cash 확인
  - 비용: 비용률 0이면 cost 전부 0·cash 정확히 0; 비용이 있으면 trade별 cost = |거래 비중| × 비용률, 체결일 비용 = 2 × 교체율 × 비용률, NAV 비율 = Π(1 − 비용분율) (상대 1e-4)
  - 오라클: 위 수치. `oracle > +500%`, `oracle > EW + 100%p`, `(stale − EW) < 0.25 × (oracle − EW)`
  - 랜덤: 8 seed 평균이 EW ± 15%p 안(허용치 근거를 docstring에)
  - lookahead: 모든 trades signal_date < fill_date, 주말 as_of는 오류, 마지막 날 시그널은 `unfilled_signal_dates`로 보고
  - drift: 리밸런싱 없는 구간에서 보유 비중 합 1 유지, 비중 비율이 수정 종가 비율대로 변화, NAV = buy&hold
  - 보류: 매일 전부 NaN이면 거래·비용 0이고 NAV = buy&hold; 일부 NaN이면 그 종목은 drift 비중 그대로(status hold, w_tgt NaN)
  - 추가: 매수 초과 시 비례 축소(status scaled), 시가 없는 종목 미체결(no_price, 현금 유지), 상장폐지 last_close/zero 손계산 일치·정책 null은 필요할 때만 오류·delisted 목록, 보유 중 분할이 손실로 잡히지 않음, CostModel 시나리오·세율표(시행일 전 날짜·없는 시장은 오류), 디스크 산출물 6개 폴더 검사
- [x] fake_dummy_base의 세 벤치마크 전략이 `--strategy all`로 v1에서 끝까지 돈다 (비용 0, 논문 비용 두 시나리오)

## [확인 필요] 항목의 probe 결과

Stage 4 본문에 `[확인 필요]` 표시는 없다. 사용자 확인 그림은 scripts/probes/stage4_figures.py.

## 명세서와 달라진 점과 이유

1. **`--costs kr`은 아직 돌지 않는다.** `costs.sell_tax_table`이 null(사용자 확인 값)이라 ConfigError로 멈춘다(공통 규칙 10). 완료 기준은 `--no-costs`와 `--costs paper`로 확인했다. 폴더 접미사는 spec의 `@paper_costs`에 더해 `@no_costs`를 두었다(kr 결과와 섞이지 않게; Stage 8 shortfall의 "v1 비용 없음" 출발점).
2. **delist_policy는 CLI 덮어쓰기로 실행했다.** 설정은 null로 두고(사용자 결정) 게이트 실행에만 `--set backtest.delist_policy=last_close`를 썼다. 덮어쓴 값은 meta.json의 config_hash·`delist_policy_used`에 반영된다. 엔진은 보유 종목이 실제로 폐지될 때만 값을 요구한다.
3. **상장폐지 판정**: "마지막 봉 이후"에만 폐지로 본다. 봉이 없지만 뒤에 다시 나오는 날(실데이터에는 없음, Stage 1 probe)은 직전 종가로 이어 간다. `zero`는 그날 NAV에서 포지션 가치를 전액 차감한다.
4. **비용의 회계**: 비용은 NAV(현금)에서 빼고 포지션 비중은 1/(1 − 비용)로 커진다. 전액 투자 포트폴리오는 체결일에 현금이 −비용만큼 음수가 되고(v1은 현금 제약이 없음, 최소 −0.3%), 다음 체결에서 가용 비중 계산(1 − 유지·매도 후 비중)이 이를 되갚는다(매수 소폭 축소). v2(Stage 7)의 cash 제약이 이 자리를 대신한다.
5. **scaled 상태가 잦은 이유**: 정지로 팔지 못한 보유분(no_price)이 있으면 그 체결일의 모든 매수가 같은 비율로 조금 줄어 `scaled`로 표시된다(EW에서 체결 30여 회 × 매수 수백 종목 = 20,618행; 축소 배율 최소 0.908, 모멘텀 0.755). 명세대로지만 상태만 보면 과해 보여 daily.csv에 `buy_scale`을 기록했다. 부동소수 오차는 1e-9 허용으로 걸러 비용 0·EW에서도 진짜 축소만 남는다.
6. **시가 없는 종목의 체결일 수익률**: 정지 종목은 시가가 없어 "전일 종가 → 기준가(종가)" 한 조각으로 처리하고 open→close 조각은 0이다.
7. **시뮬레이션 창**: 첫 as_of 종가(NAV 1)부터 `period.end`(또는 end 인자)까지. period.end 이후 체결될 시그널은 `unfilled_signal_dates`로 보고. Stage 5 라벨은 period.end 뒤의 가격을 따로 쓴다.
8. **daily.csv 추가 출력**(gross_ret, cash, n_holdings, turnover, buy_scale)과 **delisted.csv**는 spec 산출물 표에 없지만 Stage 5·8의 턴오버·비용 전후 비교와 진단에 필요해 넣었다.
9. **trades status 값**: filled, hold, scaled, no_price에 `unchanged`(목표 = 현재 비중, 거래 0)를 더했다.
10. 오라클·랜덤 테스트의 비중은 테스트 안에서 만든다(spec: "테스트 안에서만 정의"). D_strategy는 import하지 않았다.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **장난감 예제 손계산**: tests/test_stage4_engine_v1.py의 `test_toy_example_matches_hand_calculation` docstring과 본문(가격표 OPEN/CLOSE, d1·d4 체결의 단계별 산식)을 직접 따라가 보기.
2. **오라클과 오라클+1주 누적수익**: [figures/stage4_oracle_vs_stale.png](figures/stage4_oracle_vs_stale.png) (로그축). 파랑이 직선으로 치솟고(배선 정상), 주황(1주 늦춤)은 EW 아래로 떨어진다.
3. **seed별 RandomTopK 성과 분포**: [figures/stage4_random_seeds.png](figures/stage4_random_seeds.png). 평균(주황 점선) −8.3% vs EW(초록) −5.5%.
4. momentum20_topk −56%가 예상과 맞는지(단기 반전). K를 바꿔 보려면 `python -m D_strategy.run_strategy --run-id fake_dummy_base --strategy momentum20_topk --set strategies.momentum20_topk.k=50` 뒤 run_backtest.
5. 상장폐지 잔여 포지션 2건의 처리(last_close)가 타당한지. 둘 다 공개매수 후 자진 상폐라 `zero`는 부당하다.

## 질문과 미결정 사항

1. **costs.sell_tax_table** (Stage 4 선행 결정, 사용자 기입): 출처 확인 뒤 `[{market: KOSPI, start: 2024-01-01, rate: 0.0018}, {market: KOSPI, start: 2025-01-01, rate: 0.0015}, KOSDAQ 동일]` 형식으로 넣어 주세요(data_pipeline.md §8의 값은 미확인). 넣기 전까지 `--costs kr`은 멈춘다.
2. **backtest.delist_policy** (Stage 4 선행 결정): last_close | zero. 실데이터에서 발생한 2건은 자진 상폐(공개매수)라 last_close가 맞다. 정리매매 뒤 폐지는 데이터에 정리매매 가격이 남아 last_close도 이미 손실을 반영한다. **last_close를 제안**한다. 정해지면 decisions.md에 적고 설정에 넣으면 `--set` 없이 돈다.
3. **음수 현금(비용분) 처리**: 위 4번 방식(다음 체결에서 되갚음)으로 둘지, 체결 시 매수를 비용만큼 미리 줄여 현금을 0 이상으로 유지할지. 차이는 체결당 비용 크기(0.1 ~ 0.3%)의 2차 효과다. 현재 구현을 제안한다.
4. **시뮬레이션 종료일**: period.end에서 끝내 마지막 시그널(2025-06-30)은 미체결이다. 마지막 리밸런싱이 H일 동안 작동하도록 종료일을 period.end + H 거래일로 늘릴지(데이터는 2025-07-15까지 있음). 현재는 spec의 평가 기간 그대로다.
5. docs/outline.md 수정분과 논문 PDF는 여전히 커밋하지 않았다.

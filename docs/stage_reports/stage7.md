# Stage 7 보고서

2026-10-04. 범위: docs/spec.md "Stage 7. E\_backtest — 엔진 v2와 현실 제약". 다음 Stage는 시작하지 않았다.

**요약**: 현금과 주식 수를 상태로 들고 주문 단위로 체결하는 엔진 v2와 제약 6종을 구현했다. 제약을 모두 끈 v2는 v1과 일간 수익률이 1e-10 안에서 같다. 실제 예측 `kronos_base_v1`의 conf\_weighted, vol\_target, equal\_weight에 대해 누적 시나리오 7개를 만들었다. 성과를 깎는 것은 거의 전부 **비용**이고(CAGR −3.4 ~ −3.9%p), 가격제한·거래정지·유동성은 초기 자본 1억 원 기준으로 영향이 0.2%p 이하다. 사용자 결정값 5개(init\_cash, price\_limit\_pct, tick\_table, max\_participation, delist\_policy)는 설정에 null로 두고 `--set` 임시값으로 돌렸다.

## 구현한 것 (파일 목록과 한 줄 설명)

| 파일 | 설명 |
| --- | --- |
| E\_backtest/engine\_v2\_orders.py | `run_v2(weights, prices, halts, calendar, cost_model, cfg, constraints)`. 하루 순서: 기업행위(주식 수 × r) → 상장폐지 → 주문 생성 → 매도 → 매수 → 종가 평가. 일별 루프, 종목 축은 numpy 벡터 |
| E\_backtest/constraints.py | 순수 함수: `scenario_name`·`parse_scenario`, `floor_shares`, `tick_size`, `limit_prices`, `base_price`, `liquidity_cap`, `cash_scale` |
| E\_backtest/costs.py | `CostModel.components(date, markets)`: 수수료(매수·매도)·슬리피지·거래세를 따로 돌려준다 (합은 기존 `rates()`와 같다) |
| E\_backtest/run\_backtest.py | `--engine v2 [--scenario all_off\|a+b] [--shortfall]`. 기본 시나리오 = `backtest.v2.constraints`에서 켜진 것. `--shortfall` = `report.shortfall_order`의 누적 시나리오 7개 |
| common/paths.py | `backtest_scenario_dir(run_id, strategy, scenario)` → data/E\_backtest/{run\_id}/v2/{strategy}/{scenario}/ |
| tests/test\_stage7\_engine\_v2.py | 13개 (아래) |
| scripts/probes/stage7\_figures.py | 시나리오별 CAGR·Sharpe·비용·최저 현금·status 건수 표, 가격제한 거부 주문 목록, v1·v2 NAV 그림 |

산출물(시나리오 폴더마다): nav.csv(date, nav, ret, cost; nav는 init\_cash로 나눈 값), daily.csv(nav\_krw, cash, n\_holdings, turnover 포함), trades.parquet(date, signal\_date, ticker, side, target\_shares, filled\_shares, price, value, commission, tax, slippage, status), positions.parquet(date, ticker, shares, close, value, cash), meta.json, delisted.csv(있을 때).

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest -W error::FutureWarning                       124 passed (Stage 6까지 111 + test_stage7_engine_v2.py 13)
python -m E_backtest.run_backtest --run-id kronos_base_v1 --strategy {conf_weighted,vol_target,equal_weight} --engine v2 --shortfall --costs paper \
       --set backtest.delist_policy=last_close --set backtest.init_cash=100000000 --set backtest.v2.price_limit_pct=0.3 \
       --set backtest.v2.max_participation=0.1 --set 'backtest.v2.tick_table=[{min_price: 0, tick: 1}, {min_price: 2000, tick: 5}, {min_price: 5000, tick: 10},
            {min_price: 20000, tick: 50}, {min_price: 50000, tick: 100}, {min_price: 200000, tick: 500}, {min_price: 500000, tick: 1000}]'
       전략당 시나리오 7개, 시나리오 하나에 0.3 ~ 1.9초
python scripts/probes/stage7_figures.py
```

### 누적 시나리오 (kronos\_base\_v1, 논문 비용, 초기 자본 1억 원)

**conf\_weighted** (평균 보유 421종목)

| 시나리오 | 기말 NAV | CAGR | 전 단계 대비 | 총비용 | 최저 현금 비중 | status 건수 |
| --- | --- | --- | --- | --- | --- | --- |
| all\_off | 0.9928 | −0.8% | — | 0 | 0 | filled 13,114 / scaled 10,733 / no\_price 114 |
| + costs | 0.9602 | −4.2% | −3.4%p | 2.9% | −0.1% | 같음 |
| + integer\_shares | 0.9678 | −3.4% | +0.8%p | 2.9% | 2.1% | filled 21,298 / no\_price 110 |
| + cash | 0.9678 | −3.4% | 0 | 2.9% | 2.1% | 같음 |
| + price\_limit | 0.9678 | −3.4% | 0 | 2.9% | 2.1% | 같음 |
| + halt | 0.9678 | −3.4% | 0 | 2.9% | 2.1% | rejected\_halt 110 |
| + liquidity | 0.9678 | −3.4% | 0 | 2.9% | 2.1% | 같음 |

**vol\_target** (평균 보유 52종목)

| 시나리오 | 기말 NAV | CAGR | 전 단계 대비 | status 건수 |
| --- | --- | --- | --- | --- |
| all\_off | 0.9401 | −6.3% | — | filled 1,773 / scaled 1,298 / no\_price 85 |
| + costs | 0.9028 | −10.2% | −3.9%p | 같음 |
| + integer\_shares | 0.9046 | −10.0% | +0.2%p | filled 1,734 / scaled 1,280 |
| + cash | 0.9046 | −10.0% | 0 | 같음 |
| + price\_limit | 0.9026 | −10.2% | −0.2%p | rejected\_limit 1 |
| + halt | 0.9026 | −10.2% | 0 | rejected\_halt 85 |
| + liquidity | 0.9025 | −10.2% | −0.01%p | partial\_liquidity 1 |

**equal\_weight** (평균 보유 857종목): all\_off −5.7% → costs −6.9% (−1.1%p) → integer\_shares −7.6% (−0.7%p, 최저 현금 13.3%, 평균 보유 780종목) → 이후 변화 0.01%p 이하. rejected\_limit 3, rejected\_halt 161.

- **비용이 감소분의 대부분이다.** `costs` 시나리오의 기말 NAV(0.9602, 0.9028, 0.9344)는 v1 `@paper_costs` 결과와 같다.
- **정수 수량의 효과는 "현금이 남는다"로 나타난다.** equal\_weight는 1억 ÷ 857종목 = 종목당 약 12만 원이라 주가가 그보다 비싼 종목은 0주가 된다(평균 보유 857 → 780, 현금 최대 13%). 이 기간은 하락장이라 conf\_weighted·vol\_target에서는 남은 현금이 오히려 수익률을 올렸다. 이 효과의 크기와 방향은 init\_cash에 달려 있다.
- **현금 제약은 이 순서에서 효과가 0이다.** 정수 내림이 먼저 현금을 남겨 비용을 낼 여유가 생기기 때문이다. 분해 결과는 켜는 순서에 따라 달라진다(spec도 순서 고정을 요구).
- **거래정지**는 켜도 수치가 같다. 끈 상태에서도 시가가 없는 종목은 v1 규칙(no\_price)으로 거래하지 않기 때문에 status 이름만 바뀐다.
- **유동성**: 초기 자본 1억 원에 참여율 10%면 거의 걸리지 않는다(전체에서 1건).

### 가격제한으로 거부된 주문 (모든 제약 시나리오)

| 전략 | 체결일 | 종목 | 방향 | 시가 | 전일 종가 | 시가 등락 |
| --- | --- | --- | --- | --- | --- | --- |
| vol\_target | 2025-06-24 | 288330 | 매수 | 1,592 | 1,225 | +29.96% (상한가) |
| equal\_weight | 2024-08-21 | 299660 | 매도 | 9,970 | 14,240 | −29.99% (하한가) |
| equal\_weight | 2025-03-18 | 085810 | 매도 | 675 | 964 | −29.98% (하한가) |
| equal\_weight | 2025-04-22 | 054300 | 매수 | 1,370 | 1,054 | +29.98% (상한가) |

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (13개)
  - **일치성**: 제약을 모두 끈 v2와 v1의 일간 수익률 차이 < 1e-8 (fake\_dummy\_base의 벤치마크 전략 3개, 실측 최대 8e-11). 보류(NaN)가 있는 장난감 예제에서도 1e-12 안에서 일치
  - **장난감 예제**: 정수 수량 + 현금 + 비용을 켠 3종목 예제를 손계산(docstring에 계산 과정)과 일치: 주식 수, 현금, NAV, 비용, 주문의 target·filled·value·commission
  - **분할**: 1:5 분할일 전후 NAV 불변(주식 수 10,000 → 50,000). 5:1 병합 + 정수 수량에서 단수주를 현금으로 지급하고 NAV 불변
  - **가격제한**: 시가가 상한가인 종목의 매수가 거부되고 그 비중(30%)이 현금으로 남는다. 하한가 매도도 거부. 제약을 끄면 체결
  - **거래정지**: 정지 종목의 매도 주문이 거부되고 직전 가격으로 평가되며 다음 날 다시 내지 않는다
  - **유동성**: 거래대금 × 참여율을 넘는 주문이 축소된다 (1,010주 → 252주)
  - **불변식**: 모든 제약을 켠 임의 포트폴리오에서 현금 ≥ 0, 주식 수 > 0이고 정수, 포지션 가치 + 현금 = NAV, trades의 비용 합 = nav의 비용 합
  - **lookahead**: 모든 trades에서 signal\_date < date
  - 제약 함수 단위 테스트(호가단위 반올림, 시나리오 이름), 사용자 값이 null이면 명확한 오류, 누적 시나리오 7개의 이름
- [x] 실제 run\_id(kronos\_base\_v1)로 누적 시나리오 7개 생성. **단, 대상 전략은 `report.shortfall_strategy`(topk)가 아니라 conf\_weighted·vol\_target·equal\_weight다.** topk의 실제 예측(kronos\_paper\_v1)이 없기 때문이다.

## [확인 필요] 항목의 probe 결과

Stage 7 본문에 `[확인 필요]` 표시는 없다. 구현 중 확인한 데이터 사실:

- 거래정지일(55,114행)은 data/A\_prepared/prices에서 open이 전부 NaN이고 close는 직전 종가가 유지된다. 정지가 아닌데 open이 없는 행은 없다. 그래서 "시가 없음"과 "거래정지"가 같은 집합이다.
- data/A\_prepared/adj\_factor의 일간 비율 F(t)/F(t−1)이 events의 r과 같다(예: 000040 2024-03-18, 원주가 465 → 기준가 1,624, r 0.2863). v2는 events 표 대신 이 비율로 주식 수를 조정한다.

## 명세서와 달라진 점과 이유

1. **v1의 "매수 축소" 규칙을 v2의 기본 규칙으로 넣었다.** v1은 보류·시가 없음으로 남은 포지션 때문에 목표 비중만큼 살 여유가 없으면 매수 전체를 같은 비율로 줄인다(status `scaled`). spec의 "cash 끄면 현금이 음수가 되어도 체결"만 따르면 all\_off v2가 v1과 일간 수익률 최대 0.3%까지 어긋났다. 일치성이 이 Stage의 게이트라 v1 규칙을 모든 시나리오에 두었다. 이 규칙은 비용을 보지 않으므로 costs를 켜고 cash를 끄면 현금은 낸 비용만큼 음수가 된다(최저 −0.1%). `cash` 제약은 여기에 비용까지 현금 안에 들어오게 한다(status `scaled_cash`).
2. **기업행위는 events 표가 아니라 조정계수 비율로 처리한다.** 위 probe대로 같은 값이고, v1과 정확히 같은 수익률이 된다. 유상증자를 가치 보존으로 다루는 한계는 spec과 같다(납입 대금을 무시).
3. **정수 수량에서 기업행위로 생긴 단수주는 기준가로 현금 지급한다.** spec에 없던 규칙이다.
4. **상·하한가 반올림**: 상한가는 호가단위로 내림, 하한가는 올림(가격제한폭 안쪽으로). spec은 "tick\_table로 반올림"이라고만 적었다.
5. **status 추가**: spec의 4개(scaled\_cash, rejected\_limit, rejected\_halt, partial\_liquidity) 외에 `filled`, `scaled`(위 1번), `no_price`(halt를 껐을 때 시가 없는 종목). 보류(NaN) 종목은 주문이 없으므로 trades에 행이 없다.
6. **nav.csv의 nav는 init\_cash로 나눈 값**이다(v1과 같은 단위라 F\_evaluate의 지표 함수를 그대로 쓸 수 있다). 원화 금액은 daily.csv의 nav\_krw.
7. **비용 시나리오는 폴더 이름에 넣지 않았다.** `--costs paper|kr`은 meta.json의 `costs`에 기록된다. 같은 전략·시나리오를 다른 비용으로 돌리면 덮어쓴다.
8. **F\_evaluate는 v2 폴더(전략/시나리오 2단)를 아직 읽지 않는다.** 이 보고서의 CAGR·Sharpe는 probe가 nav.csv에서 직접 계산했다. v2 결과의 정식 채점과 shortfall.md는 Stage 8이다(Stage 5 보고서 "달라진 점" 8번에서 미뤄 둔 항목).
9. **`CostModel.components` 추가**(Stage 4 파일 수정): trades에 commission, tax, slippage를 따로 적기 위해서다.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **all\_off v2와 v1의 NAV 겹친 그래프**: [figures/stage7_v1_vs_v2.png](figures/stage7_v1_vs_v2.png) (굵은 회색 = v1 @no\_costs, 그 위에 v2 all\_off가 겹친다. costs, 모든 제약 시나리오 포함)
2. **status별 건수 표**와 **가격제한 거부 주문 4건**(위)
3. data/E\_backtest/kronos\_base\_v1/v2/{strategy}/{scenario}/ 의 trades.parquet, positions.parquet, meta.json
4. 이월된 삭제(샌드박스가 막음): `git rm B_model_infer/pod_bundle.py B_model_infer/pod/setup_pod.sh`, `rm -r data/pod_bundles`

## 질문과 미결정 사항

1. **임시값 5개를 확정해 configs/base.yaml에 넣어 주세요.** 이번 실행에 `--set`으로 쓴 값:
   - `backtest.init_cash: 100000000` (1억 원). 정수 수량 효과의 크기를 좌우한다. equal\_weight처럼 종목이 많은 전략은 1억으로 종목당 12만 원이라 현금이 13%까지 남는다.
   - `backtest.v2.price_limit_pct: 0.3`
   - `backtest.v2.tick_table`: 1 / 5 / 10 / 50 / 100 / 500 / 1,000원 (경계 2,000 / 5,000 / 20,000 / 50,000 / 200,000 / 500,000원). 2023년 1월 개편 이후 KRX 호가단위로 알고 있는 값이며 **출처 확인이 필요하다.**
   - `backtest.v2.max_participation: 0.1`
   - `backtest.delist_policy: last_close`
2. **shortfall 대상 전략**: `report.shortfall_strategy: topk`는 paper 예측이 있어야 한다. Stage 8의 shortfall.md를 conf\_weighted로 만들지, paper 추론을 먼저 돌릴지.
3. **현금 제약의 순서**: 지금 순서(costs → integer\_shares → cash)에서는 cash의 기여가 0으로 나온다. cash를 integer\_shares 앞에 두면 "비용을 현금 안에 맞추는 효과"가 따로 보인다. `report.shortfall_order`는 사용자 설정이다.
4. **미체결 주문의 재시도**: spec대로 다음 리밸런싱까지 다시 내지 않는다. 주 1회 전략에서 하한가 매도가 거부되면 일주일을 더 들고 간다.
5. `costs.sell_tax_table`이 비어 있어 한국 비용(`--costs kr`)으로는 여전히 돌리지 못했다.

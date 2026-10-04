# Stage 8 보고서

2026-10-04. 범위: docs/spec.md "Stage 8. G\_report — 비교와 분해". 로드맵의 마지막 Stage다.

**요약**: 전략 비교 리포트(compare.md)와 shortfall 분해 리포트(shortfall.md)를 만드는 G\_report와, C → G를 한 번에 돌리는 `scripts/run_pipeline.py`를 구현했다. 실제 예측 `kronos_base_v1`로 명령 하나에 두 리포트가 생성된다. **`kronos_paper_v1`은 추론하지 않아 "run\_id 두 개에 걸친 비교표 한 장"은 실제 예측으로는 만들지 못했다**(가짜 예측 두 run\_id로는 테스트에서 확인).

## 구현한 것 (파일 목록과 한 줄 설명)

| 파일 | 설명 |
| --- | --- |
| G\_report/compare.py | 순수 함수. `ic_table`(시그널·naive 대조군 IC 요약), `strategy_table`(행 = `{strategy}@{run_id}[@costs]`, 비용 없는 쌍은 열로만), `consistency_notes`(기간·벤치마크·거래일 수가 다르면 주석), `md_table`·`fmt` |
| G\_report/shortfall.py | 순수 함수. `waterfall`(v1 비용 없음 → 엔진 차이 → 제약을 순서대로), `scenario_sequence`, `canonical`, `largest_step` |
| G\_report/run\_report.py | 진입점. `--run-id a,b [--strategy] [--name] [--shortfall-strategy]` → reports/{이름}/compare.md, shortfall.md, figures/\*.png, meta.json |
| scripts/run\_pipeline.py | `--run-id --strategy [--engine v1\|v2] [--costs] [--from C] [--to G] [--set ...]`. 단계 CLI를 subprocess로 순서대로 호출하고, 실패하면 단계 이름을 출력하고 멈춘다 |
| F\_evaluate/run\_evaluate.py | `--engine v2`: 엔진 v2 시나리오를 채점해 `shortfall_metrics.csv`를 쓴다(전략별 v1 비용 없음 행 포함). portfolio\_metrics.csv는 건드리지 않는다 |
| tests/test\_stage8\_report.py | 5개 (아래) |

**리포트 구성**

- compare.md: run\_id별 IC 요약표와 분위별 실현수익률 그림 → 전략 비교표(CAGR, AER, Sharpe, MDD, IR, 연 턴오버, 하루 교체 수, 비용 전후 차이, Sharpe 95% 구간, DSR, 시도 수, 벤치마크) → 누적 NAV 그림(v1 비용 포함, 지수 참고선, 선 모양 = run\_id).
- shortfall.md: 켜는 순서와 "순서가 결과를 바꾼다"는 설명 → 전략마다 표(단계, 시나리오, CAGR, Sharpe, ΔCAGR, ΔSharpe, 총비용, 최저 현금 비중)와 워터폴 그림. v1(비용 없음)과 v2 all\_off의 차이는 "엔진 차이" 행으로 따로 둔다.

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest -W error::FutureWarning                       129 passed (Stage 7까지 124 + test_stage8_report.py 5)
python scripts/run_pipeline.py --run-id kronos_base_v1 --strategy equal_weight,momentum20_topk,random_topk,conf_weighted,vol_target \
       --engine v2 --costs paper --set backtest.delist_policy=last_close --set strategies.conf_weighted.threshold=0.5 \
       --set backtest.init_cash=100000000 --set backtest.v2.price_limit_pct=0.3 --set backtest.v2.max_participation=0.1 --set 'backtest.v2.tick_table=[...]'
       [C] 7.2s  [D] 1.0s  [E] 4.0s (v1 비용 없음)  [E] 40.0s (v2 시나리오 35개)  [F] 3.2s (v1)  [F] 2.3s (v2)  [G] 1.2s
       -> reports/kronos_base_v1/compare.md, shortfall.md, figures/ (png 7개)
python -m G_report.run_report                        설정의 compare_run_ids 사용: kronos_paper_v1은 결과가 없어 빠지고 리포트에 적힌다
```

**shortfall 결과 (kronos\_base\_v1, 논문 비용, 초기 자본 1억 원, 순서 costs → integer\_shares → cash → price\_limit → halt → liquidity)**

| 전략 | v1 비용 없음 | 모든 제약 | 합계 | 가장 큰 감소 | 엔진 차이 |
| --- | --- | --- | --- | --- | --- |
| conf\_weighted | −0.75% | −3.38% | −2.63%p | 비용 −3.42%p | 0 |
| vol\_target | −6.28% | −10.21% | −3.93%p | 비용 −3.90%p | 0 |
| equal\_weight | −5.74% | −7.58% | −1.84%p | 비용 −1.13%p | 0 |
| momentum20\_topk | −63.0% | — | — | 비용 −2.25%p | 0 |
| random\_topk | −10.0% | — | — | 비용 −9.95%p | 0 |

- 다섯 전략 모두 가장 큰 감소는 **비용**이다. 매주 거의 전량을 바꾸는 random\_topk가 −9.95%p로 가장 크다. 턴오버가 클수록 크다는 직관과 맞는다.
- 엔진 차이는 전 전략에서 0.0000%p다.
- momentum20\_topk·random\_topk의 "모든 제약" 수치는 reports/kronos\_base\_v1/shortfall.md에 있다.

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (5개)
  - 워터폴의 단계별 ΔCAGR 합 = 처음과 끝 시나리오의 CAGR 차이(ΔSharpe도), 순서를 바꿔도 합계 동일, 시나리오가 빠지면 이름을 들어 오류
  - 비교표의 행 이름 규칙(`@paper_costs` 접미사, 비용 없는 행 제외, 지수 행 한 번), 기간·벤치마크·거래일 수가 다르면 주석
  - fake\_dummy\_base + fake\_dummy\_paper 두 run\_id로 리포트가 오류 없이 생성되고, compare.md에 두 run\_id의 전략 행이 모두 있으며 그림 파일이 생긴다. 없는 run\_id는 명시 지정이면 오류, 설정 기본값이면 건너뛰고 기록
  - **리포트의 수치가 F\_metrics에서 온다**: 표의 CAGR·Sharpe·MDD·턴오버·DSR·IC가 portfolio\_metrics.csv·signal\_metrics.json의 값과 같은 문자열인지 확인하고, G\_report 코드에 F\_evaluate import나 수익률 계산(pct\_change, std, sqrt, cumprod)이 없는지 확인
  - run\_pipeline이 fake\_dummy\_base에서 `--strategy equal_weight`로 C → G를 끝까지 돌리고(단계 순서 C, D, E, E, F, G), 없는 전략이면 "stage D failed"로 멈춘다
- [ ] **부분 통과**: 실제 run\_id로 compare.md와 shortfall.md가 생성된다(kronos\_base\_v1). **kronos\_paper\_v1이 없어 실제 예측 두 run\_id의 비교표는 없다.**

## [확인 필요] 항목의 probe 결과

Stage 8 본문에 `[확인 필요]` 표시는 없다.

## 명세서와 달라진 점과 이유

1. **F\_evaluate에 `--engine v2`를 추가했다(다른 Stage 수정).** "리포트 코드에서 지표를 다시 계산하지 않는다"를 지키려면 v2 시나리오의 CAGR·Sharpe가 F\_metrics에 있어야 한다. Stage 7에서는 probe가 직접 계산했었다. 결과는 `shortfall_metrics.csv`로 따로 쓰고, 제약 시나리오는 전략 선택의 "시도"가 아니므로 trials.csv에 넣지 않는다.
2. **shortfall의 대상 전략**: 설정의 `report.shortfall_strategy`(topk)에 v2 결과가 없으면 결과가 있는 전략 전부를 싣고 그 사실을 리포트에 적는다. `--shortfall-strategy`로 지정할 수 있고, run\_pipeline은 `--strategy`를 그대로 넘긴다.
3. **설정의 run\_id가 없을 때**: `--run-id`를 주지 않으면(설정 기본값) 결과 없는 run\_id를 건너뛰고 리포트 첫머리에 적는다. `--run-id`로 직접 준 run\_id가 없으면 오류다.
4. **그림의 NAV 선은 data/E\_backtest의 nav.csv를 그대로 그린다.** 지표는 다시 계산하지 않지만 시계열 자체는 F\_metrics에 없어서 E 산출물을 읽는다.
5. **run\_pipeline의 E 단계는 두 번 돈다.** v1이면 비용 포함 + 비용 없음(비용 전후 차이용), v2면 v1 비용 없음(워터폴의 시작점) + v2 누적 시나리오. v2일 때 F도 v1·v2 두 번 돈다.
6. **`--strategy`의 일부만 주고 파이프라인을 돌리면 portfolio\_metrics.csv에 그 전략만 남는다**(F\_evaluate의 기존 동작). 벤치마크가 equal\_weight면 equal\_weight를 함께 넣어야 한다. 테스트는 끝에 전체를 다시 채점해 되돌린다.
7. reports/는 gitignore 대상이라 커밋되지 않는다. 이 보고서용으로 그림 두 장을 docs/stage\_reports/figures/에 복사했다.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **reports/kronos\_base\_v1/compare.md** 전체. 누적 NAV: [figures/stage8_nav.png](figures/stage8_nav.png)
2. **reports/kronos\_base\_v1/shortfall.md** 전체. 워터폴(conf\_weighted): [figures/stage8_shortfall_conf_weighted.png](figures/stage8_shortfall_conf_weighted.png). 가장 큰 감소는 비용이고, 정수 수량은 이 기간(하락장)에서 남은 현금 때문에 오히려 CAGR을 올렸다(+0.79%p). 직관과 맞는지 확인이 필요하다.
3. 이월된 삭제(샌드박스가 막음): `git rm B_model_infer/pod_bundle.py B_model_infer/pod/setup_pod.sh`, `rm -r data/pod_bundles`

## 질문과 미결정 사항

1. **kronos\_paper\_v1 추론**(약 7.3시간, $5.4)을 돌리면 Stage 6·7·8의 남은 완료 기준(TopK 실제 결과, topk shortfall, 두 run\_id 비교표)이 한 번에 채워진다. 추론 뒤 명령: `python scripts/run_pipeline.py --run-id kronos_paper_v1 --strategy topk,equal_weight,momentum20_topk,random_topk --engine v2 ...` 후 `python -m G_report.run_report`.
2. **`--set`으로 넘기고 있는 사용자 결정값 7개**를 configs/base.yaml에 확정해 주세요: `backtest.delist_policy`, `backtest.init_cash`, `backtest.v2.price_limit_pct`, `backtest.v2.tick_table`(출처 확인 필요), `backtest.v2.max_participation`, `strategies.conf_weighted.threshold`, `costs.sell_tax_table`(비어 있어 한국 비용으로는 아직 한 번도 돌리지 못했다). 확정되면 파이프라인 명령이 `--run-id`와 `--strategy`만으로 줄어든다.
3. **paper run\_id의 벤치마크 전략 프로필**: equal\_weight 등은 설정의 profile이 base라 paper run\_id에서는 `--set strategies.<name>.profile=paper`가 필요하다. 프로필별 설정을 따로 둘지.
4. Stage 6에서 남긴 질문(평균 회귀 성향, TopK 유니버스 대응안, 유니버스 이탈 시 강제 매도, nan\_in\_window)은 그대로 열려 있다.

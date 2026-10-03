# Stage 3 보고서

2026-10-03. 범위: docs/spec.md "Stage 3. D_strategy — 인터페이스와 벤치마크". Stage 2 후속 수정(spec 우선, docs/stage_reports/stage2.md 끝 절)과 같은 커밋이다. 다음 Stage는 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

| 파일 | 설명 |
| --- | --- |
| D_strategy/base.py | `Strategy(ABC)`: name / profile / schedule, `__init__(params, seed)`(profile·schedule 필수, schedule은 weekly·daily만), `reset()`, 추상 `weights(date, signals, prev_w)`. 헬퍼 `equal_weights(tickers)`, `top_k(scores, k)`(유한 점수만, 동점은 ticker 오름차순), `check_weights(w, signals, name, date)`(시그널 밖 종목·음수·무한·NaN 제외 합 > 1 거부), `require_param` |
| D_strategy/registry.py | `@register`(클래스 name ≠ 파일명이면 ImportError), `discover()`(base·registry·run_strategy·__init__ 제외 모듈 자동 import, 등록 없는 모듈은 오류), `available()`, `resolve("a,b"|"all")`, `get_strategy(name, cfg)`(cfg.strategies.<name>에서 params, seed = project.seed) |
| D_strategy/equal_weight.py | 그날 시그널이 있는 모든 종목 1/n |
| D_strategy/momentum20_topk.py | mom20(기본 signal_col) 상위 K 동일가중. K null이면 명확한 오류 |
| D_strategy/random_topk.py | rng(seed, date)로 정렬된 종목 목록에 난수 점수 → 상위 K 동일가중. 행 순서와 무관, 같은 seed 같은 결과, 날짜마다 다른 추첨 |
| D_strategy/run_strategy.py | `--run-id --strategy a,b|all`. manifest.profile == 전략 profile 검사, weekly = rebalance_dates(period.start, period.end, H) (시그널 없는 날짜가 있으면 오류), daily = 시그널의 모든 as_of, reset 후 날짜 순서로 weights 호출·계약 검사·입력 불변 검사, 0 비중 행 제거·보류 NaN 유지, `validate_weights` 후 저장, 전략마다 `{strategy}.meta.json`(보유 수·교체율 진단 포함) |
| common/meta.py, common/paths.py | `write_meta(..., filename=)`, `read_meta(..., filename=)`, `Paths.weights_meta_path(run_id, strategy)` (한 run_id 폴더에 전략 여러 개가 있어 전략별 메타 파일) |
| configs/base.yaml | `strategies.momentum20_topk.k: 20`, `strategies.random_topk.k: 20` **잠정값**(옛 설정 값, "[사용자 확정 필요]" 주석) |
| tests/test_stage3_strategy.py | 12개 (아래) |
| scripts/probes/stage3_figures.py | 사용자 확인용 그림 |
| README.md | 실행 명령·구조 |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest -W error::FutureWarning                                    67 passed (Stage 2까지 55 + test_stage3_strategy.py 12)
python -m D_strategy.run_strategy --run-id fake_dummy_base --strategy all
  equal_weight     49 dates, holdings/date 785 ~ 1,028 (mean 857.3), turnover/rebalance mean 0.071 (0.047 ~ 0.111)
  momentum20_topk  49 dates, holdings 20, turnover mean 0.539 (0.300 ~ 0.800)
  random_topk      49 dates, holdings 20, turnover mean 0.972 (0.900 ~ 1.000)
python scripts/probes/stage3_figures.py                          docs/stage_reports/figures/stage3_holdings_turnover.png
```

교체율 = 연속한 목표 비중의 Σ|Δw|/2 (1.0 = 전량 교체). EqualWeight의 7%는 유니버스 출입(시그널 종목 수 변화)에서만 나온다. RandomTopK는 매주 거의 전량 교체(기대값: 20종목 중 20/850이 겹칠 확률이라 ≈ 0.98), Momentum은 절반 정도 유지된다.

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (12개)
  - 등록 전략 전부(레지스트리 자동 수집, parametrize): 비중 ≥ 0, NaN 제외 합 ≤ 1 + 1e-9, 시그널 밖 종목 없음, 입력 불변, 같은 입력 같은 출력, 계약 위반 출력은 check_weights가 거부
  - 레지스트리: 파일명 == 클래스 name == 설정 키, resolve("all") == 폴더 전략 파일 수(3), 잘못된 name 등록은 ImportError
  - profile 불일치(base 전략을 paper run_id에) → run_strategy ValueError("profile")
  - TopK 계열은 정확히 min(K, 종목 수)개 보유, mom20 NaN 종목은 제외
  - RandomTopK: 같은 seed 같은 결과, 다른 seed 다른 결과, 날짜별 다른 추첨, 행 순서 무관
  - 동점(mom20 전부 같음) 시 결과가 실행·행 순서와 무관하게 ticker 오름차순 K개
  - K null이면 명확한 오류, weekly/daily 스케줄 날짜 선택, weekly 날짜에 시그널 없으면 오류, 0 비중 미저장·보류 NaN 저장·교체율 손계산 일치, 실제 생성물(49일, 시그널 ⊆ 유니버스, 합 1)
- [x] fake_dummy_base run_id로 `--strategy all`이 세 전략의 비중 파일을 생성한다

## [확인 필요] 항목의 probe 결과

Stage 3 본문에 `[확인 필요]` 표시는 없다. 사용자 확인 그림은 scripts/probes/stage3_figures.py.

## 명세서와 달라진 점과 이유

1. **비중 파일의 날짜 컬럼 이름은 `as_of_date`** (spec: rebalance_date). Stage 0의 common/schema WEIGHT_COLUMNS가 as_of_date로 확정돼 있고 시그널·예측과 같은 키 이름을 쓰는 편이 조인에 안전하다(레포 기준, 공통 규칙 2). 값은 리밸런싱일(=시그널일)이며 체결은 다음 거래일이다(E 단계).
2. **K 잠정값 20**: 선행 결정(momentum20_topk·random_topk의 K)이 아직 없다. 완료 기준(`--strategy all` 실행)을 위해 옛 설정 값 20을 넣고 주석에 "[사용자 확정 필요]"를 달았다. 확정되면 값만 바꾸면 된다("질문" 1).
3. **전략별 meta 파일**: 한 run_id 폴더에 전략 여러 개의 parquet가 함께 있어 meta.json 하나로는 전략을 구분할 수 없다. `{strategy}.meta.json`으로 저장하고 `write_meta`에 filename 인자를 추가했다(공통 규칙 13의 취지 유지).
4. **run_one이 입력 불변을 실행 시점에도 검사**한다(deep copy 비교). 테스트뿐 아니라 실행 중에도 전략이 시그널을 고치면 중단된다.
5. **momentum20_topk의 시그널 컬럼**: `signal_col` 파라미터(기본 mom20)를 받는다. signal.mom_window를 바꾸면 컬럼 이름이 mom{W}로 바뀌므로 그때 설정에서 지정한다.
6. **prev_w**: 보류(NaN) 종목은 직전 목표 비중을 이어받은 값으로 prev_w에 들어간다(run_one의 `eff`). 세 벤치마크 전략은 prev_w를 쓰지 않는다. Stage 6의 TopK(Top-k/Drop-n)가 이 값을 쓴다.
7. 지수 Buy&Hold는 spec대로 전략 파일로 만들지 않았다(Stage 5 벤치마크 시계열).

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **전략별 날짜당 보유 종목 수와 교체율**: [figures/stage3_holdings_turnover.png](figures/stage3_holdings_turnover.png) (위 표). RandomTopK 교체율 ≈ 0.97, Momentum ≈ 0.54, EqualWeight ≈ 0.07이 직관과 맞는지.
2. configs `strategies.momentum20_topk.k`, `strategies.random_topk.k`의 잠정값 20.
3. `--strategy` 인자 형식: `all` 또는 쉼표 구분(공백 허용, 중복 제거, 모르는 이름은 오류).

## 질문과 미결정 사항

1. **K 확정** (docs/decisions.md "아직 정하지 않은 것", Stage 3): momentum20_topk·random_topk의 K. 잠정 20. 참고: 유니버스 종목 수 785 ~ 1,028, 논문 재현(Stage 6) TopK는 k 50(CSI 300 대응) 또는 200(CSI 800 대응)이라, 벤치마크 K를 Kronos 전략의 K와 같게 둘지(비교 공정성) 정해야 한다. 결정이 나면 decisions.md에 D-14로 적고 값을 바꾸면 된다.
2. **보류(NaN) 규약의 run_strategy 쪽 처리**: 비중 파일에는 NaN이 그대로 남고, prev_w에는 직전 목표 비중이 이어진다. 개요서의 "보류 규약 승인" 항목이 아직 미결정이면 이 구현을 기준으로 확인 바란다.
3. docs/outline.md 수정분과 논문 PDF는 여전히 커밋하지 않았다.

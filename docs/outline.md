# Kronos-investing 자작 백테스트 엔진 프로젝트 개요서

Oct 1, 2026 · @진영

## 한눈에 보기

Kronos-base의 zero-shot 예측으로 KOSPI·KOSDAQ 롱온리 포트폴리오를 운용했을 때의 성과를, 직접 설계한 백테스트 엔진으로 측정한다. 엔진을 직접 만드는 이유는 체결 시점, 비용, 생존편향 같은 가정 하나하나가 결과를 얼마나 바꾸는지 손으로 확인하기 위해서다.

| 항목 | 내용 |
| --- | --- |
| 연구 질문 | Kronos 예측이 비용과 시장 제약을 반영한 뒤에도 수익을 내는가 |
| 모델 | Kronos-base + Kronos-Tokenizer-base, zero-shot (파인튜닝 없음) |
| 데이터 | KRX Open API 일별 OHLCV + 거래대금 (원주가) |
| 유니버스 | KOSPI·KOSDAQ, point-in-time 구성종목 |
| 평가 기간 | 2024-07-01 \~ 2025-06-30 (1년). Kronos-base 학습 데이터가 2024-06까지라 그 이후만 out-of-sample이다 |
| 리밸런싱 | base 프로필: 주간 (H = 5거래일). paper 프로필(논문 재현): 매 거래일 순위 갱신, 최소 보유 5일. 모두 t일 종가로 시그널을 만들고 t+1일 시가에 체결 |
| 추론 설정 | 일봉, 프로필 두 개. **base**: lookback 400, H 5, T 1.0, top\_p 0.9, N 20. **paper**: lookback 90, H 10, T 0.6, top\_p 0.9, N 10 (원 논문 §4.2.3·표 6 투자 시뮬레이션 설정) |
| 실행 환경 | 추론은 RunPod GPU, 백테스트는 로컬 Windows CPU |
| 제외 | 미국 종목, 모델 파인튜닝, 실거래 연결, 외부 백테스트 프레임워크(Qlib·NautilusTrader) |
| 성공 기준 | 높은 수익이 아니다. 같은 입력에서 같은 결과가 나오고, 각 가정이 성과에 준 영향이 수치로 분해되면 성공이다 |

## 목표

목표는 연구 결과 세 가지와, 엔진을 만들며 체득할 개념 여섯 가지다. 연구 목표는 "무엇을 측정하는가"이고, 학습 목표는 "끝나고 나면 스스로 설명할 수 있어야 하는 것"이다.

**연구 목표**

1. **예측의 품질**: 전략과 무관하게, Kronos 예측이 실제 수익률의 순위를 맞히는지 측정한다 (IC, 분위 스프레드).
2. **전략의 성과**: 예측의 서로 다른 정보(순위, 분산, 변동폭)를 쓰는 전략 3개를 벤치마크 4개와 비교한다.
3. **가정의 비용**: 이상적인 엔진에서 출발해 현실 제약을 하나씩 켤 때마다 성과가 얼마씩 줄어드는지 분해한다.
4. **논문 재현**: 원 논문(§4.2.3, 부록 D.3.3, 표 6)이 중국 A주(CSI 300·CSI 800)에서 돌린 Top-k/Drop-n 투자 시뮬레이션을 추론·전략 설정 그대로 한국 시장에 적용하면 어떤 결과가 나오는지 본다. TopK 전략이 이 역할을 맡는다 (spec 부록 C).

**학습 목표**

| 개념 | 끝나고 답할 수 있어야 하는 질문 | 다루는 단계 |
| --- | --- | --- |
| 체결 시점과 lookahead | 시그널 날짜와 체결 날짜가 하루 어긋나면 성과가 얼마나 부풀려지는가 | E, tests |
| 생존편향과 기업행위 | 상장폐지 종목을 빼거나 분할을 무시하면 무엇이 왜곡되는가 | A |
| 비중 drift와 턴오버 | 리밸런싱 사이에 비중이 왜 변하고, 그게 비용에 어떻게 이어지는가 | E |
| 거래비용 | 수수료, 거래세, 슬리피지가 연 수익률을 몇 %p 깎는가 | E, G |
| 체결 제약 | 정수 수량, 현금 부족, 가격제한, 거래정지, 유동성이 각각 얼마를 잃게 하는가 | E, G |
| 통계적 유의성 | 이 Sharpe가 운인지, 여러 번 시도해서 나온 값인지 어떻게 구분하는가 | F |

## 백테스트의 핵심 개념

백테스트는 과거의 어느 날에 서서, 그날 알 수 있던 정보로만 결정하고, 그 결정을 이후 가격으로 채점하는 시뮬레이션이다. 결과의 신뢰도는 엔진의 화려함이 아니라 가정이 얼마나 정확한지로 정해진다. 아래 표는 결과를 틀리게 만드는 대표적 오류와 이 프로젝트의 대응이다.

| 오류 | 무엇이 문제인가 | 이 프로젝트의 대응 | 단계 |
| --- | --- | --- | --- |
| Lookahead bias | 결정 시점에 몰랐던 정보를 쓴다 | as\_of\_date 기록, 입력 슬라이싱 검사, t+1 체결, 오라클 테스트 | B, E |
| 생존편향 | 지금 살아남은 종목으로만 과거를 테스트한다 | point-in-time 유니버스, 상장폐지 종목 유지 | A |
| 기업행위 왜곡 | 분할·증자일의 원주가 급변을 수익률로 오인한다 | 원주가 + 조정계수, 수익률 계산 때만 적용 | A, E |
| 비중 고정 착시 | 매일 같은 비중을 곱하면 비용 없이 매일 리밸런싱한 셈이 된다 | 리밸런싱 사이에는 수량을 고정하고 비중이 움직이게 둔다 | E |
| 비용 누락 | 수수료, 거래세, 슬리피지를 빼먹는다 | 시장·기간별 비용 모델, 비용 전후 성과를 함께 보고 | E |
| 불가능한 체결 | 상한가 매수, 거래정지 종목 매매, 소수점 주식을 가정한다 | 엔진 v2에서 제약별로 처리 | E |
| 과최적화 | 여러 파라미터를 시도하고 잘 나온 것만 본다 | 시도 횟수 기록, Deflated Sharpe, 부트스트랩 신뢰구간 | F |
| 운 | 우연히 좋은 기간에 맞았다 | 랜덤 시그널 대조군, 레짐별 분해 | D, F |

**불변 원칙** (모든 단계에 적용하고, 코드와 테스트로 강제한다)

1. t일 종가까지의 정보로 만든 시그널은 t+1일 시가에만 체결된다. 엔진은 as\_of\_date < fill\_date를 assert한다.
2. 추론 입력은 df\[df.date <= as\_of\_date\]로만 자른다. 미래 행이 한 줄이라도 있으면 예외를 던진다.
3. 전략 코드는 실현 수익률(라벨)을 입력받지 않는다. 라벨은 F\_evaluate에서만 계산한다.
4. 유니버스는 point-in-time이다. 리밸런싱일에는 그 날짜 이하의 최신 구성종목만 쓰고, 상장폐지 종목을 사후에 지우지 않는다.
5. 가격은 원주가로 저장하고 조정계수는 별도 컬럼으로 둔다. 조정은 그 시점까지 알려진 계수로만 한다.

## 전체 흐름과 디렉토리 구조

데이터는 A에서 G까지 한 방향으로만 흐른다. 각 단계는 앞 문자 단계가 남긴 파일만 읽고, 자기 문자의 data 폴더에만 쓴다. 폴더 문자와 산출물 문자가 같으므로, 어떤 파일이 어디서 왔는지 이름만 보고 알 수 있다.

1. **A\_data\_prepare**: KRX 원천 데이터 → 정제 가격, 조정계수, 유니버스, 거래일 달력
2. **B\_model\_infer**: 가격 → Kronos 원시 예측 샘플 (RunPod)
3. **C\_signal**: 예측 샘플 → 날짜·종목별 시그널
4. **D\_strategy**: 시그널 → 리밸런싱일별 목표 비중
5. **E\_backtest**: 목표 비중 + 가격 → 체결 내역, 보유 수량, NAV
6. **F\_evaluate**: 시그널·NAV → 예측 지표와 성과 지표
7. **G\_report**: 지표 → 전략 비교 리포트, 성과 감소 분해

```text
Kronos-investing/
├── configs/
│   └── base.yaml                  # 모든 파라미터 (설정 파일은 하나)
├── common/                        # 공용 패키지: 설정, 경로, 스키마, 달력, lookahead 검사, 메타 기록
├── A_data_prepare/                # 기존 data_prepare/ (KRX 수집·정제·검증, docs/data_pipeline.md) + Stage 1
│   ├── run.py krx_client.py transform.py universe.py sanity.py manifest.py ...   # 수집 파이프라인 (완료)
│   ├── build_prices.py            # 정제 가격 표 (원주가, 거래대금, 상장주식수)
│   ├── build_calendar.py          # 거래일, 종목별 거래정지 표
│   ├── build_adj_factor.py        # 전진 누적 조정계수 + events 표
│   ├── build_universe.py          # point-in-time 유니버스
│   └── run_prepare.py             # 진입점 -> data/A_prepared/
├── B_model_infer/                 # 기존 infer/ (RunPod 전용)
│   ├── build_batch.py
│   ├── backends.py
│   ├── run_inference.py
│   ├── make_fake_predictions.py   # 로컬 검증용 더미·오라클 예측
│   └── validate_predictions.py    # 실제 예측 폴더 검사 (Stage 6)
├── RunPod/                        # Pod 운영: local.sh runpod.sh setup_runpod.sh push_meta.sh inputs.sha256.json (부록 C, D-18)
├── C_signal/
│   ├── aggregate.py               # 샘플 → exp_ret, std, p_up, pred_range
│   ├── baseline_features.py       # mom20, vol20, rev5
│   └── run_signal.py
├── D_strategy/
│   ├── base.py                    # Strategy 인터페이스
│   ├── registry.py
│   ├── equal_weight.py            # 전략 하나 = 파일 하나 (이름 = 파일명 = 설정 키)
│   ├── momentum20_topk.py
│   ├── random_topk.py
│   ├── topk.py                    # 논문 재현: Top-k/Drop-n, 매일, 최소 보유 5일 (paper 프로필)
│   ├── conf_weighted.py
│   ├── vol_target.py
│   └── run_strategy.py            # --run-id --strategy a,b|all
├── E_backtest/
│   ├── engine_v1_weights.py       # 엔진 v1: 비중 공간, 벡터화, 이상적 체결
│   ├── engine_v2_orders.py        # 엔진 v2: 주문 단위, 일별 루프
│   ├── costs.py
│   ├── constraints.py             # 가격제한, 거래정지, 호가단위, 유동성
│   └── run_backtest.py
├── F_evaluate/
│   ├── labels.py                  # 실현 수익률 (라벨은 여기서만)
│   ├── signal_metrics.py          # IC, 분위, 적중률, 캘리브레이션
│   ├── portfolio_metrics.py       # CAGR, Sharpe, MDD 등
│   ├── significance.py            # 부트스트랩, Deflated Sharpe
│   └── run_evaluate.py
├── G_report/
│   ├── compare.py
│   ├── shortfall.py
│   └── run_report.py
├── scripts/
│   ├── run_pipeline.py            # --run-id --strategy ... 로 C→D→E→F→G CLI를 순차 호출 (단계 import 금지)
│   └── probes/                    # [확인 필요] 항목을 실제로 확인하는 1회성 스크립트
├── data/
│   ├── krx_raw/<snapshot>/        # KRX 원본 JSON 빈티지 (data_pipeline.md, 유지)
│   ├── raw/ universe/ MANIFEST.json   # 수집 파이프라인 산출물 (data_pipeline.md, 유지)
│   ├── A_prepared/                # prices, adj_factor, events, universe, calendar, halts
│   ├── B_predictions/{run_id}/    # as_of=YYYY-MM-DD.parquet, manifest.json
│   ├── C_signals/{run_id}/
│   ├── D_weights/{run_id}/{strategy}.parquet
│   ├── E_backtest/{run_id}/{engine}/{strategy}/   # nav, trades, positions, meta.json
│   └── F_metrics/{run_id}/        # 지표, trials.csv
├── reports/{run_id}/              # G 산출물 (사람이 읽는 리포트)
├── docs/                          # outline.md spec.md data_pipeline.md stage_reports/
└── tests/
```

폴더 이름을 문자(A\_ \~ G\_)로 시작하게 한 이유는 Python 패키지로 쓰기 위해서다. 숫자로 시작하는 폴더는 `import` 문으로 불러올 수 없다. 단계 폴더는 `__init__.py`를 가진 패키지이고 실행은 `python -m E_backtest.run_backtest`처럼 한다. 다만 import가 가능해졌다고 단계끼리 import하지는 않는다. 여러 단계가 함께 쓰는 로직은 모두 `common/` 패키지에 두고, 단계 사이는 data/ 아래 파일로만 연결한다. 같은 이유로 엔진은 전략 코드를 import하지 않고, D가 저장한 목표 비중 파일만 읽는다. A\_data\_prepare의 수집·검증 부분은 이미 구현되어 있고([data_pipeline.md](data_pipeline.md)), Stage 1은 그 산출물 위에 data/A\_prepared/를 만든다.

## 단계별 설계

각 단계는 하는 일, 산출물, 핵심 개념, 완료 기준 순서로 정리한다. 학습의 중심은 E\_backtest이고, 나머지 단계는 E에 깨끗한 입력을 주고 결과를 정직하게 채점하기 위해 존재한다.

### A\_data\_prepare: 기준 데이터 만들기

- **하는 일**: KRX Open API로 수집한 일별 OHLCV와 거래대금(수집 완료)을 정제한다. 이후 모든 단계가 이 데이터 하나를 기준으로 쓴다.
- **산출물**: prices (date, ticker, market, open, high, low, close, volume, value; 원주가), adj\_factor (date, ticker, factor), universe (date, ticker), calendar (거래일, 종목별 거래정지 여부)
- **이미 있는 것** ([data_pipeline.md](data_pipeline.md), 코드 `data_prepare/`): 스냅샷 2026-09-28의 원본 JSON, 거래소별 `prices.parquet`(원주가, 거래대금, 시총, 상장주식수, `halted`, `adj_factor`), 조정 이벤트 표, 유니버스 스냅샷 3변형(base / liq5 / clean), 벤치마크 지수·ETF, sanity 게이트 7개, sha256 매니페스트. Stage 1은 원본 JSON을 다시 파싱하지 않고 이 산출물을 입력으로 쓴다.
- **핵심 개념**: KRX Open API는 원주가를 준다. 분할·무상증자일에 가격이 끊기므로, 수익률 계산과 모델 입력 모두 조정계수가 필요하다. 조정계수는 KRX의 전일대비(`CMPPREVDD_PRC`)로 기준가 변경을 역산해 이미 구했고, 등락률(`FLUC_RT`)과 전 행 일치로 검증했다(§5-2). 현재 계수는 마지막 행이 1인 후진 누적이므로 Stage 1에서 첫날이 1인 전진 누적 F로 바꾼다. 두 날짜 사이 비율은 같다. 유상증자처럼 할인 발행이 있으면 권리락 기준가 조정이 신주 인수 여부에 따른 실제 수익과 다를 수 있다. 현금배당은 기준가 조정이 없어 계수에 들어가지 않는다(가격수익률).
- **완료 기준**: 분할 종목 샘플에서 조정 수익률이 연속이다. 날짜별 유니버스 종목 수가 스냅샷과 일치한다. 상장폐지 종목이 폐지일까지 남아 있다.

### B\_model\_infer: 예측 만들기

- **하는 일**: 추론 프로필이 정한 as\_of 날짜마다(base: H = 5 간격, paper: 매 거래일) Kronos predict\_batch를 호출하고, 샘플 경로를 집계하지 않은 채 원시 그대로 저장한다. 이미 있는 날짜 파일은 건너뛰어 중단 후 재개할 수 있게 한다.
- **추론 프로필**: lookback, pred\_len(H), as\_of 간격, T, top\_p, sample\_count 묶음을 `infer.profiles`에 이름을 붙여 둔다. run\_id 하나는 프로필 하나를 따르고 manifest.json에 `profile`을 기록한다. base는 이 프로젝트의 기본 설정, paper는 논문 투자 시뮬레이션 설정이다. paper는 날짜 수 5배, 샘플 절반, pred\_len 2배, lookback 1/4이라 GPU 시간이 base의 몇 배 든다(RunPod 비용은 결정 사항).
- **산출물**: as\_of=YYYY-MM-DD.parquet (스키마 고정: as\_of\_date, ticker, horizon\_step 1..H, sample\_id, pred\_open, pred\_high, pred\_low, pred\_close, pred\_volume), manifest.json (profile, 모델명, HF revision, lookback, pred\_len, step, T, top\_p, sample\_count, 유니버스, 실행 시각, 커밋 해시)
- **로컬 검증용 가짜 예측**: make\_fake\_predictions.py는 같은 스키마로 두 가지를 만든다. 더미 예측(현재가 + 노이즈)은 Kronos 없이 파이프라인을 끝까지 돌리기 위한 것이다. 오라클 예측(실제 미래 가격)은 엔진의 lookahead 배선을 시험하기 위한 것이다.
- **완료 기준**: 입력 슬라이싱 검사 통과, manifest 존재, 스키마 검증 통과

### C\_signal: 예측 분포를 숫자로 줄이기

- **하는 일**: 날짜·종목마다 N개 샘플 경로를 아래 시그널로 요약한다. last\_close는 as\_of\_date 종가다. H와 N은 그 run\_id의 manifest에서 읽는다.

| 시그널 | 정의 | 담는 정보 |
| --- | --- | --- |
| exp\_ret | mean(pred\_close\[H\]) / last\_close − 1 (H번째 스텝만) | 기대 방향과 크기 |
| exp\_ret\_mean | mean over 샘플·스텝 1..H of pred\_close / last\_close − 1 | 논문 시그널 R\_{t→t+H} = (1/H Σ p̂\_{t+i} − p\_t) / p\_t. 경로 평균 기대수익 |
| std | std(pred\_close\[H\]) / last\_close | 예측의 불확실성 |
| p\_up | 샘플 중 pred\_close\[H\] > last\_close 비율 | 상승 확률 |
| pred\_range | mean(pred\_high − pred\_low) / last\_close | 예측된 변동폭 |
| mom20, vol20, rev5 | 과거 가격만으로 계산 | 벤치마크·대조군용 |

- **핵심 개념**: 같은 예측 분포라도 어떤 숫자로 줄이느냐가 전략의 성격을 정한다. last\_close와 pred\_close는 모델 입력과 같은 가격 기준(원주가 또는 수정주가)이어야 한다.
- **완료 기준**: (날짜, 종목) 중복 없음, 그날 유니버스 밖 종목 없음

### D\_strategy: 시그널을 목표 비중으로

- **하는 일**: 리밸런싱일마다 시그널을 받아 종목별 목표 비중을 낸다. 모든 전략은 하나의 인터페이스를 따른다.
- **구조**: 전략 하나 = 파일 하나(`D_strategy/{name}.py`), 이름 = 파일명 = 설정 키 = 레지스트리 키. registry.py가 폴더의 전략 모듈을 자동으로 찾아 등록하므로 파일을 추가하면 끝이다. 각 전략은 설정에서 `profile`(어느 추론 프로필의 예측을 쓰는지), `schedule`(weekly / daily), `signal_col`, 고유 파라미터를 받는다. 모든 단계 CLI(run\_strategy, run\_backtest, run\_evaluate, run\_report)는 `--strategy a,b,c | all`로 대상을 고르고, `scripts/run_pipeline.py --run-id ... --strategy ...`가 C→G를 한 번에 돌린다.
- **상태를 가진 전략**: run\_strategy는 날짜를 오름차순으로 한 번씩 호출하고 시작 시 `reset()`을 부른다. 최소 보유일처럼 과거 결정을 기억해야 하는 전략은 내부 상태를 가질 수 있다. 다만 상태는 자기 과거 출력에서만 만들어야 하고 가격·라벨은 볼 수 없다.
- **보류(hold) 비중**: 반환값에서 비중이 NaN인 종목은 "거래하지 말고 현재 포지션을 유지"라는 뜻이다. 엔진은 그 종목의 drift된 비중을 그대로 둔다. 매일 순위를 갱신하되 보유 종목은 건드리지 않는 논문 전략(최대 n개만 교체)을 비중 파일로 표현하기 위한 규약이다.

```python
class Strategy(ABC):
    name: str          # == 파일명 == 설정 키
    profile: str       # 쓰는 예측의 추론 프로필 (base | paper)
    schedule: str      # weekly | daily
    def reset(self) -> None: ...   # run_strategy 시작 시 호출 (상태 초기화)
    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        """signals: 해당 date의 ticker × 시그널. 반환: ticker → 목표 비중, NaN 제외 합 <= 1 (나머지는 현금).
        NaN = 보류(현재 포지션 유지). 날짜 오름차순으로 한 번씩 호출된다."""
```

| 전략 | 쓰는 예측 정보 | 비중 규칙 |
| --- | --- | --- |
| TopK (논문 재현, Top-k/Drop-n) | 순위 (exp\_ret\_mean) | 매일 순위를 갱신한다. 보유 K개 중 최소 보유 5일을 넘긴 종목 가운데 "보유 + 미보유 상위 n개" 합집합 순위의 하위 n개를 팔고, 판 수만큼 미보유 상위 종목을 1/K씩 산다. 남은 보유 종목은 보류(NaN). 원 논문 CSI 300 k 50·n 5, CSI 800 k 200·n 10 |
| ConfidenceWeighted | 분산 | exp\_ret / std에 비례, 임계치 미만이면 현금 |
| VolTarget | 변동폭 | exp\_ret 상위 K개를 pred\_range 역수로 가중 |
| EqualWeight | 없음 (벤치마크) | 유니버스 전체 동일가중 |
| Momentum20 TopK | 없음 (벤치마크) | mom20 상위 K개 동일가중 |
| RandomSignal | 없음 (대조군) | seed 고정 난수 시그널로 TopK |
| 지수 Buy&Hold | 없음 (벤치마크) | 시장 지수 보유 |

- **산출물**: data/D\_weights/{run\_id}/{strategy}.parquet (리밸런싱일 × 종목 목표 비중, 보류는 NaN). run\_id는 전략의 profile과 같은 프로필의 예측이어야 하며 아니면 run\_strategy가 멈춘다
- **핵심 개념**: 전략은 라벨을 볼 수 없다. 비중을 파일로 넘기므로 prev\_w는 실제 보유가 아니라 직전 목표 비중이다. 회전율을 줄이는 전략을 만들 때 이 차이를 기억한다.
- **완료 기준**: 비중 합 ≤ 1(NaN 제외), 음수 없음, 유니버스 밖 종목 없음. TopK는 하루 교체 종목 수 ≤ n, 최소 보유일 위반 0

### E\_backtest: 엔진 두 개를 직접 만들기

엔진을 두 단계로 만든다. v1은 개념을 확인하는 이상적 엔진이고, v2는 현실 제약을 하나씩 켤 수 있는 엔진이다. 둘의 차이가 이 프로젝트의 핵심 산출물이 된다.

**엔진 v1 (engine\_v1\_weights.py, 비중 공간·벡터화)**

- 목표 비중을 t+1 시가에 체결하고, 시가→시가 수익률로 NAV를 굴린다.
- 리밸런싱 사이에는 수량을 고정한다. 가격이 움직이면 비중도 따라 움직이고, 다음 리밸런싱의 턴오버는 이 drift된 비중 기준으로 계산한다.
- 소수점 주식, 무제한 체결을 허용한다. 비용은 턴오버 × 비용률로 차감한다.

- 보류(NaN) 종목은 거래하지 않고 drift된 비중을 유지한다. 매수 비중 합이 가용 비중(1 − 보류·유지 비중 합)을 넘으면 매수를 비례 축소한다. 매일 리밸런싱하는 전략도 같은 규칙으로 처리하므로 스케줄은 엔진에 영향을 주지 않는다.

**엔진 v2 (engine\_v2\_orders.py, 주문 단위·일별 루프)**

- 현금과 종목별 보유 수량을 상태로 들고 하루씩 진행한다.
- 체결일 시가에 매도를 먼저 하고, 확보된 현금 안에서 매수한다. 보류(NaN) 종목은 주문을 만들지 않는다.
- 아래 제약을 설정에서 하나씩 켜고 끌 수 있다.

| 제약 | 처리 방식 |
| --- | --- |
| 정수 수량 | 목표 금액 / 시가를 내림해 주식 수 결정, 남는 돈은 현금 |
| 현금 부족 | 매도 대금 + 보유 현금을 넘는 매수는 비례 축소 |
| 가격제한 | 시가가 상한가면 매수 불가, 하한가면 매도 불가 (전일 종가 ±30%, 호가단위 반올림 근사) |
| 거래정지 | 매매 불가, 보유 중이면 가치는 직전 가격으로 동결 |
| 상장폐지 | 처리 규칙을 정해 일관 적용 (결정 사항) |
| 유동성 | 주문 금액을 당일 거래대금의 일정 비율 이하로 제한 |

**비용 모델 (costs.py)**: 매수·매도 수수료, 매도 시 증권거래세, 슬리피지(bp)로 구성한다. 거래세는 시장별·시행일별 세율표로 관리하고 값은 직접 확인해 채운다. 수수료 0.015%와 슬리피지 5bp는 기존 설계의 기본값이며 설정으로 바꿀 수 있다.

- **산출물**: nav.csv, trades.parquet, positions.parquet, meta.json
- **완료 기준**: 손으로 계산한 3종목·5일 장난감 예제와 두 엔진 결과가 일치한다. 모든 제약을 끈 v2가 v1과 허용 오차 안에서 같다. 오라클 테스트를 통과한다.

### F\_evaluate: 채점하기

- **하는 일**: 예측과 포트폴리오를 따로 채점한다. 실현 수익률(라벨)은 체결과 같은 기준인 "as\_of 다음날 시가 → H일 뒤 시가"로 여기서만 계산한다. H는 run\_id의 프로필을 따른다(base 5, paper 10).
- **예측 지표 (전략과 무관)**: 횡단면 Rank IC 평균, ICIR, IC 시계열, exp\_ret 5분위별 실현수익률과 Q5−Q1 스프레드, 방향 적중률(p\_up > 0.5), 캘리브레이션(예측 std와 실현 절대오차의 상관), naive 대조군(랜덤워크, Momentum20, 평균회귀) IC 비교, 반기·상승장·하락장별 분해
- **포트폴리오 지표**: CAGR, 연변동성, Sharpe, Sortino, Calmar, MDD와 회복기간, 월별 수익률 분포 / 벤치마크 대비 초과수익(연율화 = 논문의 AER), 베타, 알파, Tracking Error, Information Ratio(논문의 IR), 월별 hit ratio / 연평균 턴오버, 비용 전후 CAGR 차이, 평균 보유종목 수, 하루 평균 교체 종목 수
- **유의성**: 부트스트랩 Sharpe 95% 신뢰구간(수익률 자기상관을 고려해 블록 부트스트랩), Deflated Sharpe Ratio(trials.csv에 쌓인 시도 횟수 반영)
- **핵심 개념**: 지표는 직접 구현한다. quantstats는 시각화와 교차 검증에만 쓴다.
- **완료 기준**: 주요 지표가 quantstats 계산과 일치한다.

### G\_report: 비교하고 분해하기

- **compare.md**: 지정한 run\_id들(`--run-id a,b`) 아래 선택한 전략들의 지표를 한 표로 모으고 누적수익 차트를 붙인다. 프로필이 다른 전략(base의 conf\_weighted·vol\_target과 paper의 TopK)은 같은 기간·같은 유니버스·같은 벤치마크로 맞춰 `{strategy}@{run_id}` 행으로 나란히 비교한다. 시그널 평가는 run\_id마다 따로 표를 만든다(H가 다르므로).
- **run\_pipeline**: `scripts/run_pipeline.py --run-id ... --strategy ...`가 C→D→E→F→G의 CLI를 순서대로 호출한다. 전략 하나만 빠르게 보거나 전부 다시 돌릴 때 쓴다.
- **shortfall.md**: 엔진 v1(비용 없음)에서 출발해 비용 → 정수 수량 → 현금 → 가격제한 → 거래정지 → 유동성 순으로 제약을 켜며 CAGR·Sharpe 감소를 워터폴로 보인다. 분해 결과는 켜는 순서에 따라 달라지므로 순서를 고정하고 리포트에 적는다.
- **완료 기준**: 명령 하나로 리포트 두 개가 생성되고, run\_id 두 개(base·paper)에 걸친 전략 비교표가 한 장에 나온다.

## 검증 장치

엔진이 맞는지는 결과를 보고 판단하지 않고, 정답을 아는 입력을 넣어 확인한다. 아래 테스트가 모두 통과하기 전에는 Kronos 결과를 해석하지 않는다.

| 테스트 파일 | 확인하는 것 | 단계 |
| --- | --- | --- |
| test\_adj\_factor.py | 분할·증자일 전후 조정 수익률이 연속이다 | A |
| test\_universe\_pit.py | 리밸런싱일 유니버스가 그날 이하 최신 스냅샷과 같고, 폐지 종목이 남아 있다 | A |
| test\_no\_lookahead.py | 미래 행이 섞인 추론 입력은 예외가 난다. 모든 체결이 as\_of\_date < fill\_date를 만족한다 | B, E |
| test\_strategies.py | 비중 합 ≤ 1, 음수 없음, 유니버스 밖 종목 없음 (등록된 모든 전략 자동 포함) | D |
| test\_topk\_dropout.py | 하루 교체 ≤ n, 최소 보유일 준수, 보유 수 K 유지, 보류(NaN) 종목은 엔진에서 거래 0 | D, E |
| test\_engine\_toy.py | 손으로 계산한 3종목·5일 예제와 두 엔진의 NAV가 일치한다 | E |
| test\_oracle.py | 실제 미래 수익률을 시그널로 넣으면 비정상적으로 높은 수익이 나오고, 하루 늦추면 그 우위가 사라진다 | E |
| test\_random\_baseline.py | RandomSignal 전략이 비용 차감 후 EqualWeight 근처에 있다 | D, E |
| test\_engine\_consistency.py | 제약을 모두 끈 v2가 v1과 허용 오차 안에서 같다 | E |
| test\_metrics.py | 주요 지표가 quantstats 계산과 일치한다 | F |

오라클 테스트는 엔진의 배선을, 랜덤 테스트는 비용과 유니버스의 편향을 잡아낸다. 랜덤 전략이 EqualWeight보다 크게 좋거나 나쁘면 시그널이 아니라 엔진이나 데이터에 문제가 있다는 뜻이다.

## 설정과 재현성

같은 run\_id와 같은 설정이면 언제 돌려도 같은 숫자가 나와야 한다. 이를 위해 다음 규칙을 지킨다.

- **설정은 하나**: 비용, 세율표, K, 임계치, 기간, seed, 제약 on/off를 모두 configs/base.yaml에 둔다. 코드에 숫자 상수를 쓰지 않는다.
- **외부 사실값은 직접 확인**: 거래세율처럼 법이 정하는 값은 출처를 확인해 채우고, 설정 파일에 주석으로 출처를 남긴다.
- **실행 기록**: 모든 실행은 meta.json에 run\_id, 엔진, 전략, 설정 해시, git 커밋 해시, 입력 데이터 해시를 남긴다.
- **난수 고정**: RandomSignal을 포함한 모든 난수는 설정의 seed로 고정한다.
- **시도 기록**: 파라미터 조합을 바꿔 돌릴 때마다 data/F\_metrics/{run\_id}/trials.csv에 한 줄씩 쌓는다. Deflated Sharpe가 이 횟수를 쓴다.
- **연결은 파일로만**: 단계끼리 import하지 않는다. 공용 로직은 common/에만 둔다.
- **Windows 실행**: 멀티프로세싱을 쓰는 스크립트는 진입점을 if **name** == "**main**": 아래에 둔다.

## 구현 순서

가짜 예측으로 파이프라인을 끝까지 먼저 완성하고, 실제 Kronos 예측은 마지막에 붙인다. 그래야 엔진 버그와 모델 성능을 섞지 않고 따로 볼 수 있다. 각 단계는 괄호 안의 게이트를 통과해야 다음으로 넘어간다.

1. **A 마무리**: 조정계수, 유니버스, 달력 생성 (test\_adj\_factor, test\_universe\_pit 통과)
2. **B 가짜 예측**: 더미·오라클 예측 생성기 (스키마 검증 통과)
3. **C 시그널 집계** (중복·유니버스 검사 통과)
4. **D 전략 구조 + 벤치마크 전략**: 파일 분리·레지스트리·`--strategy` 인자·보류 규약, EqualWeight, RandomTopK, Momentum20부터 (test\_strategies 통과)
5. **E 엔진 v1 + 비용** (test\_engine\_toy, test\_oracle, test\_random\_baseline 통과)
6. **F 지표** (test\_metrics 통과)
7. **D Kronos 전략 3개 + 실제 예측 연결**: TopK는 논문 Top-k/Drop-n 재현(paper 프로필), conf\_weighted·vol\_target은 base 프로필. 실제 예측 run\_id 두 개를 연결한다. 이 시점에 처음으로 Kronos 결과를 본다
8. **E 엔진 v2**: 제약을 하나씩 추가 (test\_engine\_consistency 통과)
9. **G 리포트와 shortfall 분해, run\_pipeline** (명령 하나로 리포트 생성, run\_id 간 전략 비교)

## 결정·확인할 사항

아래 항목은 구현 전에 정해야 결과가 흔들리지 않는다. 가장 먼저 볼 것은 추론 입력의 가격 기준이다. 원주가로 추론했다면 분할일 부근 예측이 오염되어 있어, 백테스트 쪽 수정만으로는 해결되지 않는다.

- [x] Kronos 추론 입력의 가격 기준: `infer/build_batch.py`는 lookback 윈도우를 as\_of 시점 조정계수로 rebase한다. 윈도우 안의 분할은 제거되고 마지막 종가는 as\_of의 원주가와 같으므로 예측은 "as\_of 원주가 스케일"이다. 시그널의 last\_close도 as\_of 원주가를 쓴다(`price_basis: raw`). 재추론 불필요. 실제 Kronos 예측은 아직 없다
- [x] 조정계수 생성 방법: 기준가 역산으로 확정 (data\_pipeline.md §5-2, FLUC\_RT와 100% 일치, 지수 재구성 상관 1.0000 / 0.9999). Stage 1은 후진 누적 → 전진 누적 변환과 events 표 저장만 한다
- [x] 거래정지일 표현: 응답에 남되 시가·고가·저가 0, 거래량 0, 종가(기준가)만 유효 → `halted=True` (data\_pipeline.md §5-4, 전체 행의 2.1%). 응답에서 빠지는 것은 상장폐지뿐. Stage 1 probe는 캐시된 JSON으로 이 규칙을 재확인만 한다
- [x] 평가 기간: 2024-07-01 \~ 2025-06-30으로 확정. Kronos-base 학습 데이터 종료(2024-06) 이후 1년이 out-of-sample 구간이다. 현재 스냅샷의 가격(\~ 2025-07-15)이 라벨(H = 5)까지 덮는다
- [ ] 거래소별 분석 여부: 기본은 KOSPI·KOSDAQ 풀링 run\_id 하나다. 거래소별 실행이나 F\_evaluate의 거래소별 IC·성과 분해를 넣을지 정한다 (data\_pipeline.md §9)
- [ ] 유니버스 정의: 전 종목 + 필터 방식이 이미 구현되어 있다 (보통주, 스팩 제외, 이력 ≥ 400봉, 20일 평균 거래대금 ≥ 10억, 당일 무거래 제외; data\_pipeline.md §4). base / liq5(5억) / clean(관리종목·투자주의환기 제외, KOSDAQ만) 중 어느 변형을 본 표본으로 쓸지 정한다
- [ ] 시장별·시행일별 증권거래세율 표. data\_pipeline.md §8 기준 2024년 0.18%, 2025년 0.15%(두 거래소 동일)로 알려져 있으나 출처를 확인해 설정에 기입한다
- [ ] 초기 운용 자금 (정수 수량 효과의 크기를 좌우)
- [ ] 상장폐지 종목 처리 규칙 (마지막 거래가로 청산할지, 잔존가치 0으로 볼지). 데이터에는 정리매매 가격이 폐지일까지 남아 있다(§4). 유니버스 구성 종목 중 구간 내 폐지는 KOSPI 2, KOSDAQ 7종목
- [ ] 전략 파라미터 기본값 (ConfidenceWeighted 임계치, VolTarget·벤치마크의 K)
- [ ] 논문 재현의 유니버스·K·n 대응: 논문은 CSI 300(약 300종목)에 k 50·n 5, CSI 800에 k 200·n 10. 한국 대응안 (a) 코스피 시총 상위 200(point-in-time, `universe.top_n_mktcap`)에 k 50·n 5 ≈ CSI 300, (b) 코스피+코스닥 풀링 base(약 800\~1,100종목)에 k 200·n 10 ≈ CSI 800. 둘 다 돌릴지, 하나만 할지
- [ ] 논문 재현의 벤치마크 지수(AER·IR 기준): 코스피 지수, 코스닥 지수, 또는 유니버스 EqualWeight
- [ ] 비용 시나리오: 한국 실제 비용(기본) 외에 논문 비용(매수 0.10%, 매도 0.15%, 공식 레포 qlib 설정)으로도 돌려 비교할지
- [ ] paper 프로필 추론 비용 감수: 매 거래일 × 전 종목 × N 10 × pred\_len 10 (base의 몇 배). 유니버스를 (a)로 줄이면 비용도 준다
- [ ] 보류(NaN) 비중 규약 승인: 논문 전략의 "보유 종목은 건드리지 않음"을 비중 파일로 표현하는 방식. 신규 매수는 1/K 고정이라 Qlib(남은 현금 전부 배분)와 조금 다르다
- [ ] 유동성 한도 (주문 금액 / 당일 거래대금 비율)

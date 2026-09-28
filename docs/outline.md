# 프로젝트: Kronos-base 모델을 실제 투자에 적용했을 때의 성과 검증

## 목적
Kronos-base(https://github.com/shiyu-coder/Kronos, HF: NeoQuasar/Kronos-base + Kronos-Tokenizer-base)를
zero-shot 예측기로 사용해 롱온리 주식 포트폴리오를 운용했을 때의 성과를 lookahead bias 없이 측정한다.
대상: 미학습 구간 2024-07-01 ~ 2025-06-30, 유니버스는 우선 KOSPI200 + S&P500 (설정으로 확장 가능).
기본 설정: 일봉, lookback=400, horizon H=5(주간 리밸런싱), T=1.0, top_p=0.9, sample_count=20.

## 1. Lookahead bias 방지 조치 (코드로 강제)
- 예측 행마다 as_of_date(추론 입력의 마지막 날짜)를 기록한다. 백테스트 엔진은 as_of_date < fill_date를 assert한다.
- 추론 입력은 반드시 df[df.date <= as_of_date] 로 슬라이싱한다. 미래 행이 한 줄이라도 포함되면 예외를 던진다.
- 가격은 조정주가가 아닌 원가격(open/high/low/close/volume) + 별도 adj_factor 컬럼을 사용한다.
  수익률 계산 시에만 as_of 시점까지 알려진 조정계수를 적용한다.
- 유니버스는 point-in-time으로 관리한다: data/universe/constituents_{index}.parquet (date, ticker)에서
  각 리밸런싱일 당시의 구성종목만 사용한다. 상장폐지 종목을 사후 제거하지 않는다.
- 시그널은 t일 종가 기준으로 생성하고, 체결은 t+1일 시가로 고정한다. 엔진은 가중치 행렬을 shift(1)한 뒤
  시가→시가 수익률과 곱한다.
- tests/test_no_lookahead.py 에서 위 규칙을 자동 검증한다.

## 2. 추론(RunPod GPU) / 백테스트(로컬 CPU) 분리
두 환경은 "예측 parquet 파일"이라는 계약으로만 연결된다. 추론 코드는 백테스트를 import하지 않고, 그 반대도 마찬가지.

### 2-1. 추론 (infer/, RunPod에서만 실행)
- run_inference.py --run-id <id> --start --end --step H --lookback --sample-count ... CLI 스크립트로 작성 (노트북 금지).
- 리밸런싱 날짜(step=H 간격)에만 추론한다. 날짜별로 predict_batch를 호출한다
  (모든 시계열은 동일 lookback·pred_len이어야 하므로 build_batch.py에서 정렬·필터링).
- 출력: data/predictions/{run_id}/as_of={YYYY-MM-DD}.parquet 로 날짜 단위 저장.
  재개 가능해야 한다: 시작 시 이미 존재하는 날짜 파일은 건너뛴다 (spot pod 중단 대비).
- data/predictions/{run_id}/manifest.json 에 모델명, HF revision, lookback, pred_len, T, top_p, sample_count,
  유니버스, 실행 시각, 코드 커밋 해시를 기록한다.
- 샘플 경로를 집계하지 않고 원시 그대로 저장한다.

### 2-2. 예측 parquet 스키마 (고정)
as_of_date(date), ticker(str), horizon_step(int, 1..H), sample_id(int),
pred_open, pred_high, pred_low, pred_close, pred_volume (float)

### 2-3. 백테스트 (backtest/, 로컬에서만 실행)
- signals.py: 예측 parquet를 읽어 (as_of_date, ticker)별 집계 시그널 생성.
  exp_ret = mean(pred_close[H]) / last_close - 1, std = std(pred_close[H])/last_close,
  p_up = P(pred_close[H] > last_close), pred_range = mean(pred_high - pred_low)/last_close,
  그리고 벤치마크 전략용 mom20, vol20 등 가격 기반 피처.
- engine.py: 벡터화 엔진. 입력은 가중치 행렬 W(dates × tickers)와 시가 행렬. shift(1) 체결, 비용 차감, NAV·턴오버·보유종목수 산출.
  전략이 무엇인지 모르는 순수 함수로 작성.
- costs.py: 시장별 비용. KR: 매도세 0.18% + 수수료 0.015% + 슬리피지 5bp, US: 수수료 0 + 슬리피지 5bp (설정 가능).

## 3. 전략 플러그인 파이프라인
- backtest/strategies/base.py 에 단일 인터페이스:
    class Strategy(ABC):
        name: str
        def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
            """signals: 해당 date의 ticker × 피처. 반환: ticker→목표비중, 합<=1 (나머지 현금)"""
- 전략은 파일 하나에 클래스 하나. 레지스트리(dict 또는 entry point)로 이름만으로 로드.
- 구현할 Kronos 전략 3개 (각각 예측의 다른 정보를 사용):
  1) topk.py            TopK: exp_ret 상위 K 동일가중 (예측의 순위만 사용)
  2) conf_weighted.py   ConfidenceWeighted: exp_ret/std 비례 가중, 임계 미만이면 현금 (예측의 분산 사용)
  3) vol_target.py      VolTarget: exp_ret 상위 K를 pred_range 기반 역변동성 가중 (예측의 리스크 사용)
- 벤치마크 전략 (같은 인터페이스, baselines.py): EqualWeight, Momentum20 TopK, RandomSignal(seed 고정), 지수 Buy&Hold.
- 실행: scripts/run_backtest.py --run-id <id> --strategy topk --config configs/base.yaml
  결과는 results/{run_id}/{strategy}/ 에 nav.csv, weights.parquet, metrics.json, config.yaml 저장.
- scripts/compare.py: results/{run_id}/ 아래 모든 전략을 읽어 지표 비교표 + 누적수익 차트 생성.

## 4. Metric 세트 (backtest/metrics.py 에 직접 구현, 시각화만 quantstats 사용 가능)
### 4-1. 자산운용 실무 지표
- 수익·위험: CAGR, 연변동성, Sharpe, Sortino, Calmar, MDD, MDD 회복기간, 월별 수익률 분포
- 벤치마크 대비: 초과수익, 베타, 알파, Tracking Error, Information Ratio, 월별 hit ratio
- 거래: 연평균 턴오버, 비용 차감 전후 CAGR 차이, 평균 보유종목 수
- 유의성: 부트스트랩 Sharpe 95% 신뢰구간, Deflated Sharpe Ratio(시도한 파라미터 조합 수 반영)
### 4-2. 예측모델 기반 전략 특화 지표 (전략과 무관하게 예측 자체를 평가)
- 횡단면 Rank IC 평균, ICIR, IC 시계열 차트
- exp_ret 분위(5분위)별 실현수익률과 Q5-Q1 스프레드
- 방향 적중률(p_up>0.5 vs 실현 방향), 예측 std와 실현 절대오차의 상관(캘리브레이션)
- naive 대조군 대비: 랜덤워크(예측=현재가), Momentum20, 평균회귀 시그널의 IC와 비교
- 레짐별 분해: 반기별, 상승장/하락장별 IC 및 전략 성과

## 5. 디렉토리 구조·테스트·구현 순서
kronos-invest/
├── configs/base.yaml
├── data/{raw,universe,predictions}/
├── infer/{build_batch.py, run_inference.py}
├── backtest/{signals.py, engine.py, costs.py, metrics.py, report.py, strategies/}
├── scripts/{run_backtest.py, compare.py, download_data.py}
├── results/
└── tests/{test_no_lookahead.py, test_engine_sanity.py, test_strategies.py}

- tests/test_engine_sanity.py 필수 2가지:
  (a) 실현 미래수익률을 시그널로 넣으면 비정상적으로 높은 수익이 나와야 한다 (엔진 배선 검증)
  (b) RandomSignal 전략은 비용 차감 후 EqualWeight 근처여야 한다 (비용·유니버스 편향 검증)
- 구현 순서: 데이터 로더·유니버스 → 예측 parquet 스키마와 더미 예측 생성기 → 엔진·비용·metric
  → 테스트 통과 → 전략 3개 + 벤치마크 → compare 리포트 → 마지막에 infer/ 작성.
  (더미 예측으로 로컬 파이프라인을 먼저 완성한 뒤 RunPod 추론을 붙인다.)
- 모든 설정은 yaml 하나에서 관리하고 하드코딩 금지. 함수는 순수 함수 우선, 파일 경로는 run_id 기준으로 일관되게.
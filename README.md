# Kronos-investing

Kronos-base(https://github.com/shiyu-coder/Kronos)를 zero-shot 예측기로 사용해 롱온리 주식 포트폴리오를
운용했을 때의 성과를 **lookahead bias 없이** 측정하는 프로젝트. 설계 문서는 [docs/outline.md](docs/outline.md),
데이터 수집·처리 규칙은 [docs/data_pipeline.md](docs/data_pipeline.md).

```
configs/base.yaml          모든 설정 (하드코딩 금지)
common/                    infer/와 backtest/가 공유하는 코드 (둘은 서로 import하지 않음)
  config.py paths.py       yaml 로딩, run_id 기준 경로
  lookahead.py             slice_as_of / assert_no_future / assert_signal_before_fill
  schema.py                예측 parquet 고정 스키마 + 검증
  data.py universe.py      가격 로더(원가격 + adj_factor), point-in-time 유니버스
  synthetic.py             합성 가격/유니버스 생성기 (테스트, 드라이런)
data_prepare/              KRX Open API -> data/raw, data/universe: probe/fetch/build/benchmark/sanity/manifest 단계
infer/                     RunPod GPU에서만 실행
  build_batch.py           as_of 기준 lookback 윈도우 배치 (미래 행 포함 시 예외)
  backends.py              KronosBackend (원시 샘플 경로 보존), DummyBackend
  run_inference.py         CLI. 날짜별 parquet 저장, 재개 가능, manifest.json
backtest/                  로컬 CPU에서만 실행
  signals.py               예측 집계 시그널 + 가격 피처 (features) / realized_returns (labels, 평가 전용)
  engine.py costs.py       shift(1) 시가 체결 벡터화 엔진, 시장별 비용
  metrics.py               4-1 운용 지표, 4-2 예측 평가 지표 (직접 구현)
  report.py runner.py      결과 저장/차트, 파이프라인 결합
  strategies/              base.py 인터페이스, topk / conf_weighted / vol_target, baselines.py, 레지스트리
scripts/                   make_dummy_predictions.py, run_backtest.py, compare.py
tests/                     test_no_lookahead.py, test_engine_sanity.py, test_strategies.py, test_data_prepare.py
```

## 설치

```bash
pip install -e ".[dev]"            # 로컬 백테스트 + 테스트
pip install -e ".[infer]"          # RunPod: torch, huggingface_hub 등
git clone https://github.com/shiyu-coder/Kronos ./Kronos   # RunPod: model 패키지 (configs: model.kronos_repo)
```

## 데이터 (KOSPI + KOSDAQ, KRX Open API)

```bash
echo "<KRX AUTH_KEY>" > .env                     # 키 값만, 또는 KRX_API_KEY=...
python -m data_prepare.run probe                 # 서비스 승인·스키마 확인 (호출 ≤ 5회)
python -m data_prepare.run fetch --dry-run       # 필요한 호출 수 확인 (키당 일 10,000회)
python -m data_prepare.run                       # probe fetch build benchmark sanity manifest 전체
python -m data_prepare.run build benchmark sanity   # 캐시가 있으면 네트워크 없이 파생 단계만
python -m data_prepare.manifest --verify         # 공유받은 data/가 같은 빈티지인지 sha256 검증
```

원본 JSON은 `data/krx_raw/<snapshot>/`에 빈티지로 고정되고(`krx.snapshot`), `PULL_METADATA.json`·`BUILD_METADATA.json`·
`data/MANIFEST.json`이 수집 시각, 설정 해시, 코드 커밋, 파일 sha256을 기록한다. 로그는 `logs/data_prepare.log`.

생성 파일과 규칙은 [docs/data_pipeline.md](docs/data_pipeline.md) 참고. 형식:

- `data/raw/{index}/prices.parquet` : `date, ticker, open, high, low, close, volume, adj_factor`
  (원가격 + 누적 조정계수. `raw * adj_factor`가 수정주가. 벤치마크는 `data/raw/benchmark/prices.parquet`, `market` 컬럼 포함)
- `data/universe/constituents_{index}.parquet` : `date, ticker` 스냅샷. 각 리밸런싱일에는 그 날짜 이하 최신 스냅샷만 사용.

실데이터 없이 파이프라인 전체를 돌려보려면 `--synthetic-data` 로 합성 데이터를 생성할 수 있다.

## 실행 순서

```bash
# 1) 로컬: 더미 예측으로 파이프라인 검증 (합성 데이터 생성 포함)
python scripts/make_dummy_predictions.py --run-id dummy_rw --synthetic-data
python scripts/run_backtest.py --run-id dummy_rw --strategy all
python scripts/compare.py --run-id dummy_rw
pytest

# 2) RunPod: Kronos 추론 (재개 가능: 이미 있는 as_of 파일은 건너뜀)
python infer/run_inference.py --run-id kronos_base_v1 --config configs/base.yaml \
    --start 2024-07-01 --end 2025-06-30 --step 5 --lookback 400 --horizon 5 --sample-count 20
#    파인튜닝 모델은 --model /path/to/finetuned --tokenizer /path/to/tokenizer 로 로컬 경로 지정

# 3) 로컬: data/predictions/kronos_base_v1/ 를 내려받은 뒤
python scripts/run_backtest.py --run-id kronos_base_v1 --strategy all                   # 코스피+코스닥 풀링
python scripts/run_backtest.py --run-id kronos_base_v1 --strategy all --indices kospi    # 거래소별 (results/.../{strategy}@kospi)
python scripts/run_backtest.py --run-id kronos_base_v1 --strategy all --indices kosdaq
python scripts/compare.py --run-id kronos_base_v1
```

결과: `results/{run_id}/{strategy}/{nav.csv, weights.parquet, metrics.json, config.yaml, nav.png}`,
예측 기반 전략에는 `prediction_metrics.json`, `ic_series_*.png`, `quantiles_*.png` 추가.
`results/{run_id}/compare.{csv,md,png}` 에 전략 비교표와 누적수익 차트.

## Lookahead 방지 (코드로 강제, tests/test_no_lookahead.py)

- 추론 입력은 `slice_as_of(df, as_of)` 로만 자름. 미래 행이 있으면 `LookaheadError`.
- 예측 행마다 `as_of_date` 기록. 엔진은 체결일(다음 거래일 시가)이 `as_of_date` 보다 늦음을 assert.
- 원가격 + `adj_factor` 사용. 윈도우 내부는 as_of 시점 계수로 rebase, 수익률은 계수 비율만 사용.
- 유니버스는 point-in-time 스냅샷. 상장폐지 종목은 사후 제거하지 않음 (가격 소실 시 마지막가에 청산).
- 시그널 t일 종가 → t+1일 시가 체결, 시가→시가 수익률. 전략은 라벨(`ret_cc`, `ret_oo`)을 절대 받지 않음.

## 예측 parquet 스키마 (고정)

`as_of_date, ticker, horizon_step(1..H), sample_id, pred_open, pred_high, pred_low, pred_close, pred_volume`
— 샘플 경로를 집계하지 않고 원시 그대로 저장. Kronos는 `sample_count` 샘플을 내부에서 평균내므로
`KronosBackend`는 각 시계열을 sample_count 번 복제해 `sample_count=1`로 호출하여 원시 샘플을 유지한다.

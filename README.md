# Kronos-investing

Kronos-base(https://github.com/shiyu-coder/Kronos)의 zero-shot 예측으로 KOSPI·KOSDAQ 롱온리 포트폴리오를 운용했을 때의
성과를, **직접 설계한 백테스트 엔진**으로 lookahead bias 없이 측정하는 프로젝트.

| 문서 | 내용 |
| --- | --- |
| [docs/outline.md](docs/outline.md) | 개요서: 연구·학습 목표, 불변 원칙, 단계(A~G) 설계, 검증 장치 |
| [docs/spec.md](docs/spec.md) | 구현 명세서: Stage 0~8 작업·테스트·완료 기준, 공통 설정, 기존 코드 재사용 부록 |
| [docs/data_pipeline.md](docs/data_pipeline.md) | 데이터 파이프라인: KRX Open API 수집, 조정계수, point-in-time 유니버스, sanity 게이트 |
| [docs/stage_reports/](docs/stage_reports/) | Stage별 구현 보고서 |

## 구조

데이터는 A에서 G까지 한 방향으로 흐른다. 단계 폴더는 패키지이며 서로 import하지 않고 `common/`만 import한다.
단계 사이는 data/ 아래 파일로만 이어진다. 모든 경로는 `common/paths.py`, 모든 파라미터는 `configs/base.yaml`에서 나온다.

```
configs/base.yaml      모든 설정 (spec.md "공통 설정" 뼈대; [사용자] 값은 null로 두고 사용자가 채운다)
common/                config.py paths.py schema.py lookahead.py meta.py data.py universe.py synthetic.py
A_data_prepare/        KRX Open API -> data/raw, data/universe (docs/data_pipeline.md); run_prepare.py -> data/A_prepared/ (Stage 1)
B_model_infer/         Kronos 추론 (RunPod). build_batch.py backends.py run_inference.py make_fake_predictions.py pod_inputs.py verify_run.py checksum.py env_info.py -> data/B_predictions/{run_id}/
C_signal/              aggregate.py baseline_features.py run_signal.py -> data/C_signals/{run_id}/signals.parquet (Stage 2)
D_strategy/            base.py registry.py equal_weight.py momentum20_topk.py random_topk.py topk.py conf_weighted.py vol_target.py run_strategy.py -> data/D_weights/{run_id}/{strategy}.parquet (Stage 3)
E_backtest/            costs.py constraints.py engine_v1_weights.py engine_v2_orders.py run_backtest.py -> data/E_backtest/{run_id}/v1/{strategy}[@no_costs|@paper_costs]/ (Stage 4)
F_evaluate/            labels.py signal_metrics.py portfolio_metrics.py significance.py run_evaluate.py -> data/F_metrics/{run_id}/ (Stage 5)
RunPod/                Pod 운영 스크립트와 절차서 (README.md local.sh runpod.sh setup_runpod.sh push_meta.sh inputs.sha256.json)
G_report/              compare.py shortfall.py run_report.py -> reports/{run_id}/compare.md, shortfall.md, figures/ (Stage 8)
scripts/run_pipeline.py  C -> G 단계 CLI를 순서대로 호출
scripts/probes/        [확인 필요] 항목을 실제 데이터로 확인하는 1회성 스크립트
data/                  krx_raw/<snapshot>/ raw/ universe/ MANIFEST.json (수집 산출물, 유지) + A_prepared/ B_predictions/ ... (단계 산출물)
tests/                 test_common.py (Stage 0), test_data_prepare.py, test_no_lookahead.py
```

## 설치

```bash
pip install -r requirements.txt       # 로컬 백테스트 + 테스트 (버전 고정)
pip install quantstats                # 선택: Stage 5 지표 교차검증 테스트 (없으면 해당 테스트 skip)
pip install -r requirements-infer.txt # RunPod / 로컬 스모크: torch, huggingface_hub, einops, safetensors, tqdm (버전 고정)
git clone https://github.com/shiyu-coder/Kronos ./Kronos   # RunPod: model 패키지 (configs: model.kronos_repo)
pytest
```

## 데이터 (KOSPI + KOSDAQ, KRX Open API)

```bash
echo "<KRX AUTH_KEY>" > .env                        # 키 값만, 또는 KRX_API_KEY=...
python -m A_data_prepare.run probe                  # 서비스 승인·스키마 확인 (호출 ≤ 5회)
python -m A_data_prepare.run fetch --dry-run        # 필요한 호출 수 확인 (키당 일 10,000회)
python -m A_data_prepare.run                        # probe fetch build benchmark sanity manifest 전체
python -m A_data_prepare.run build benchmark sanity # 캐시가 있으면 네트워크 없이 파생 단계만
python -m A_data_prepare.manifest --verify          # 공유받은 data/가 같은 빈티지인지 sha256 검증
python -m A_data_prepare.run_prepare                # Stage 1: data/raw + data/universe -> data/A_prepared/ (약 6초)
```

- `data/raw/{kospi,kosdaq}/prices.parquet` : `date, ticker, open, high, low, close, volume, adj_factor, halted, trdval, ...`
  (원가격 + 조정계수. `raw * adj_factor`가 수정주가. 벤치마크는 `data/raw/benchmark/prices.parquet`)
- `data/universe/constituents_{index}[_{variant}].parquet` : `date, ticker` 거래일별 point-in-time 스냅샷
- 원본 JSON은 `data/krx_raw/<snapshot>/`에 빈티지로 고정. 데이터 파일은 커밋하지 않고 `MANIFEST.json`과 메타데이터만 커밋한다.
- `data/A_prepared/` (Stage 1, 이후 모든 단계의 입력): `prices`(date, ticker, market, OHLCV, value, listed_shares; 원주가),
  `calendar`(date), `halts`(date, ticker, is_halted), `adj_factor`(date, ticker, factor; 첫날 1인 전진 누적 F, 수정가 = raw × F),
  `events`(ticker, ex_date, ratio, r, applied, ...), `universe`(date, ticker, market; `universe.variant`), `benchmark`(지수·ETF 레벨), `meta.json`

## 추론 (RunPod GPU, docs/spec.md 부록 C·D)

```bash
# 절차서: RunPod/README.md. 코드는 git, 입력·예측은 SSH, 메타데이터는 results/<RUN_ID> 브랜치로 움직인다 (D-18)
bash RunPod/local.sh push-code "메시지"                                    # 로컬: main 커밋·push
bash RunPod/local.sh upload <host> <port>                                  # 로컬: prices + liq5 유니버스 -> pod /workspace/inputs (RunPod/inputs.sha256.json과 대조)
RUN_ID=kronos_base_v1 bash RunPod/runpod.sh                                # Pod, tmux 안에서: 설치 -> 입력 대조 -> GPU 스모크 -> 추론 -> verify_run -> checksum write -> 메타데이터 push
bash RunPod/local.sh fetch kronos_base_v1                                  # 로컬: rsync + checksum verify
bash RunPod/local.sh merge kronos_base_v1                                  # 로컬: manifest.json, checksums.json 등을 main에 병합
bash RunPod/local.sh terminate kronos_base_v1                              # 로컬: verify 통과 후 Pod 종료
python -m B_model_infer.pod_inputs write                                   # 입력 데이터가 바뀌었을 때 RunPod/inputs.sha256.json 갱신
# 로컬 CPU 스모크 (Kronos 코드 ./Kronos, torch 필요: pip install -r requirements-infer.txt)
python -m B_model_infer.run_inference --backend kronos --run-id smoke_cpu --device cpu --batch-size 4 --sample-count 2 --max-tickers 5 --allow-unpinned --start 2024-07-01 --end 2024-07-01
python -m B_model_infer.run_inference --run-id check --backend dummy --start 2024-07-01 --end 2024-07-01   # 배선 확인 (torch 불필요)
```

실제 실행 전에 `model.revision`, `model.tokenizer_revision`(HF 커밋 sha), `model.kronos_repo_commit`, `model.batch_size`(Pod probe)를 채운다. 추론 유니버스는 프로필의 `universe_variant`(liq5, D-5)이고 백테스트 유니버스(base)와의 교집합은 C_signal이 만든다(D-3).

샘플링 설정(lookback, pred_len, step, T, top_p, sample_count)은 `infer.profiles.<infer.default_profile>`에서 읽고 CLI 플래그로 덮어쓸 수 있다.
출력은 `data/B_predictions/{run_id}/as_of=YYYY-MM-DD.parquet` + `manifest.json`. 재개 가능(이미 있는 날짜는 건너뜀).
예측 스키마는 고정: `as_of_date, ticker, horizon_step(1..H), sample_id, pred_open, pred_high, pred_low, pred_close, pred_volume`.
입력 윈도우는 as_of 시점 조정계수로 rebase하므로 마지막 종가는 as_of의 원주가와 같다 (spec.md 부록 A, scripts/probes/stage0_price_basis.py).

## 실제 예측 연결 (Stage 6)

```bash
python -m B_model_infer.validate_predictions --run-id kronos_base_v1      # manifest·날짜·유니버스·스텝·가격 기준 검사 -> validation.json
python -m C_signal.run_signal --run-id kronos_base_v1
python -m D_strategy.run_strategy --run-id kronos_base_v1 --strategy all --set strategies.conf_weighted.threshold=0.5   # all = 그 run_id 프로필의 전략 전부
python -m E_backtest.run_backtest --run-id kronos_base_v1 --strategy all --costs paper --set backtest.delist_policy=last_close
python -m F_evaluate.run_evaluate --run-id kronos_base_v1 --strategy all --set strategies.conf_weighted.threshold=0.5 --set backtest.delist_policy=last_close
python scripts/probes/stage6_mean_reversion.py                             # exp_ret과 입력 구간 z의 관계
```

Kronos 전략: `topk`(paper 프로필, 매일, Top-k/Drop-n), `conf_weighted`·`vol_target`(base 프로필, 주 1회). paper 프로필 run_id에서 벤치마크 전략을 돌릴 때는 `--set strategies.<name>.profile=paper`.

## 엔진 v2와 제약 (Stage 7)

```bash
# 주문 단위 엔진. 제약 6종(costs, integer_shares, cash, price_limit, halt, liquidity)을 시나리오로 켠다 -> data/E_backtest/{run_id}/v2/{strategy}/{scenario}/
python -m E_backtest.run_backtest --run-id kronos_base_v1 --strategy conf_weighted --engine v2 --scenario all_off          # v1과 같은 결과
python -m E_backtest.run_backtest --run-id kronos_base_v1 --strategy conf_weighted --engine v2 --shortfall --costs paper   # report.shortfall_order의 누적 시나리오 7개
python scripts/probes/stage7_figures.py                                                                                    # 시나리오별 표, 가격제한 거부 주문, v1·v2 NAV 그림
```

v2는 `backtest.init_cash`가 필요하고, price_limit는 `backtest.v2.price_limit_pct`·`tick_table`, liquidity는 `max_participation`이 필요하다(null이면 오류). 값이 정해지기 전에는 `--set`으로 준다(docs/stage_reports/stage7.md).

## 리포트와 파이프라인 (Stage 8)

```bash
# C -> D -> E -> F -> G를 한 번에. 실패하면 단계 이름을 출력하고 멈춘다. --from / --to 로 구간 지정, --set 은 D·E·F·G에 전달
python scripts/run_pipeline.py --run-id kronos_base_v1 --strategy equal_weight,conf_weighted,vol_target --engine v2 --costs paper --set ...
python -m F_evaluate.run_evaluate --run-id kronos_base_v1 --engine v2      # v2 시나리오 채점 -> shortfall_metrics.csv
python -m G_report.run_report --run-id kronos_base_v1,kronos_paper_v1      # reports/{첫 run_id}/compare.md, shortfall.md, figures/
python -m G_report.run_report                                              # report.compare_run_ids 사용 (결과 없는 run_id는 건너뛰고 리포트에 적는다)
```

리포트의 수치는 모두 data/F_metrics에서 읽는다(G_report는 지표를 계산하지 않는다). 아직 정해지지 않은 사용자 값은 `--set`으로 준다(docs/stage_reports/stage8.md 질문 2).

## 가짜 예측과 시그널 (로컬, Kronos 없이)

```bash
python -m B_model_infer.make_fake_predictions --kind dummy    # data/B_predictions/fake_dummy_base/  (랜덤워크, 정보 없음)
python -m B_model_infer.make_fake_predictions --kind oracle   # data/B_predictions/fake_oracle_base/ (실제 미래 가격, 엔진 배선 테스트 전용)
python -m C_signal.run_signal --run-id fake_oracle_base       # data/C_signals/fake_oracle_base/signals.parquet + meta.json
python -m D_strategy.run_strategy --run-id fake_dummy_base --strategy all   # data/D_weights/fake_dummy_base/{strategy}.parquet (+ .meta.json)
python -m E_backtest.run_backtest --run-id fake_dummy_base --strategy all --no-costs   --set backtest.delist_policy=last_close   # 엔진 v1, 비용 0
python -m E_backtest.run_backtest --run-id fake_dummy_base --strategy all --costs paper --set backtest.delist_policy=last_close   # 논문 비용 (기본, D-15)
python -m F_evaluate.run_evaluate --run-id fake_dummy_base --strategy all   # data/F_metrics/fake_dummy_base/ (signal_metrics.json, portfolio_metrics.csv, trials.csv)
```

`--costs kr`은 `costs.sell_tax_table`이 필요하고(기본은 D-15의 논문 비용 paper), 보유 종목이 상장폐지되면 `backtest.delist_policy`가 필요하다(사용자 결정 값; 없으면 명확한 오류). `--set key=value`로 설정을 일회성으로 덮어쓴다.

`--profile paper`로 paper 프로필(매 거래일, H 10, N 10) 가짜 예측도 만든다(fake_{dummy,oracle}_paper). 전략은 `D_strategy/<name>.py` 하나에 하나이며 이름 = 파일명 = `strategies.<name>` 설정 키다.

시그널 행은 그날 유니버스 ∩ 예측이 있는 종목이다(D-3). 컬럼: `as_of_date, ticker, exp_ret, exp_ret_mean, std, p_up, pred_range, n_samples, last_close, mom20, vol20, rev5`.

## 진행

[docs/spec.md](docs/spec.md)의 "사용법"대로 한 Stage씩 진행한다. 완료: Stage 0 (기반 정리), Stage 1 (A_data_prepare 기준 데이터), Stage 2 (가짜 예측 + C_signal), Stage 3 (D_strategy 인터페이스와 벤치마크), Stage 4 (E_backtest 엔진 v1과 비용), Stage 5 (F_evaluate 채점). 다음: Stage 6 (Kronos 전략과 실제 예측 연결).

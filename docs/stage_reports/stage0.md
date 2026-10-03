# Stage 0 보고서

2026-10-03. 범위: docs/spec.md "Stage 0. 기반 정리". 새 기능 없음. 다음 Stage는 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

**패키지 이동 (복사본, 원본 삭제는 사용자 작업 — 아래 "명세서와 달라진 점" 1)**

| 파일 | 설명 |
| --- | --- |
| A_data_prepare/ (12 파일) | 기존 data_prepare/의 복사본. import 경로만 `A_data_prepare.*`로 바꿈. run.py·sanity.py는 설정 키 참조만 새 뼈대로 교체(아래 3·4), 로직 불변 |
| B_model_infer/ (4 파일) | 기존 infer/의 복사본. run_inference.py는 `period.*`·`infer.profiles.<default_profile>`에서 읽고 원자적 쓰기·커밋 해시를 common/meta.py에서 가져온다. build_batch.py·backends.py는 import 경로만 변경 |
| C_signal/ D_strategy/ E_backtest/ F_evaluate/ G_report/ | `__init__.py`만 있는 빈 패키지 (각각 어느 Stage에서 채우는지 docstring) |
| scripts/probes/README.md, scripts/probes/stage0_price_basis.py | probe 폴더와 Stage 0 probe (추론 입력 가격 기준 확인) |

**common/**

| 파일 | 공개 함수 | 변경 |
| --- | --- | --- |
| common/config.py | load_config, cfg_get, cfg_override, dump_config (유지) + **config_hash(cfg, length=12)** (키 정렬 JSON sha256), **require(cfg, key)** (null·누락이면 ConfigError), ConfigError | 추가 |
| common/paths.py | Paths: raw_dir, price_file, universe_dir, constituents_file, krx_cache_dir, logs_dir, predictions_dir, prediction_file, manifest_file (유지) + **prepared_dir, prepared_path(name), signals_dir, signals_path(run_id), weights_dir, weights_path(run_id, strategy), backtest_dir(run_id, engine, strategy), metrics_dir(run_id), report_dir(run_id)**; results_dir 제거; as_of_from_filename 유지 | 추가·제거 |
| common/schema.py | 예측 스키마(유지) + **PRICE_COLUMNS/validate_prices, SIGNAL_COLUMNS/validate_signals, WEIGHT_COLUMNS/validate_weights, weights_to_wide, NAV_COLUMNS/validate_nav** (컬럼·dtype 강제·키 유일·값 범위, 같은 스타일) | 추가 |
| common/lookahead.py | 기존 함수 유지 + alias **assert_no_future_rows** ≡ assert_no_future, **assert_fill_after_signal(trades)** ≡ assert_signal_before_fill(trades.signal_date, trades.fill_date) | 추가 |
| common/meta.py (신규) | **write_meta(out_dir, cfg, inputs, extra, root)** → meta.json(run_id, stage, strategy, engine, config_hash, git_commit, inputs{sha256,bytes}, run_at_utc), **read_meta, git_commit_hash(root), file_hash(path)**, write_json_atomic, atomic_parquet, input_hashes | 신규 |
| common/data.py | load_prices의 `backtest.default_market` 참조 제거(market은 universe.markets에 있을 때만 부여). 그 외 유지 | 수정 |
| common/synthetic.py | `benchmark.index_tickers` → `data.index_tickers` | 수정 |

**설정·환경**

| 파일 | 설명 |
| --- | --- |
| configs/base.yaml | spec "공통 설정" 뼈대로 재구성. 기존 값은 옮기고 출처를 주석에 적음. `[사용자]` 값은 null (이전 임시값은 주석) |
| requirements.txt | 이 환경에 설치된 버전으로 고정 (numpy 1.26.4, pandas 2.2.2, pyarrow 14.0.2, PyYAML 6.0.1, scipy 1.13.1, matplotlib 3.10.0, pytest 7.4.4) |
| pytest.ini | testpaths = tests, pythonpath = . (pyproject의 pytest 설정은 제거해 한 곳만 남김) |
| pyproject.toml | packages를 새 패키지 8개로 |
| .gitignore | data/predictions → data/B_predictions 외 단계 산출물 폴더(A_prepared, C_signals, D_weights, E_backtest, F_metrics), reports/ 무시 |
| tests/test_common.py (신규) | 11개: config_hash 키 순서 무관, require null 오류, 뼈대 섹션 존재, paths run_id·전략·엔진별 비겹침, validate_* 5종의 거부 조건, write_meta 내용, lookahead alias |
| tests/conftest.py, tests/test_no_lookahead.py, tests/test_data_prepare.py | 새 패키지 이름·설정 키(period.*, infer.profiles.base.*, data.index_tickers)로 수정 |
| README.md, docs/data_pipeline.md | 명령·경로를 새 이름으로 (data_pipeline.md는 실행 명령 줄만) |

### 레포 현황 조사 (작업 1)

- 코드: common/ 7 모듈, data_prepare/ 12 파일(수집 파이프라인), infer/ 4 파일, tests/ 3 파일(22 테스트). backtest/·scripts/·results/와 테스트 2개는 사전 정리에서 삭제(git index에 staged 상태로 남아 있음).
- data/: krx_raw/2026-09-28 914 MB(원본 JSON, 3,638 파일), raw/ 43 MB(kospi 15.5 MB·kosdaq 29.5 MB prices.parquet, benchmark 92 KB, adjustment_events·validation·diag·residual csv/json), universe/ 644 KB(constituents 3변형 × 2거래소 + coverage csv), MANIFEST.json 600 KB(3,665 파일), predictions/ 빈 폴더(.gitkeep).
- prices.parquet 컬럼: date, ticker, open, high, low, close, volume, adj_factor, exchange, name, sect, trdval, mktcap, list_shrs, chg, fluc_rt, halted. kospi 569,720행, 2022-10-04 ~ 2025-07-15. benchmark: KOSPI, KOSDAQ, 226490, 229200 (market 컬럼 있음). constituents: 2024-06-03 ~ 2025-07-15, kospi 103,458행.
- common/ 공개 함수: spec 부록 A 표와 일치함을 확인(config.py, paths.py, lookahead.py, schema.py, data.py, universe.py, synthetic.py).
- 환경: macOS, /opt/anaconda3 Python 3.12.4. torch 없음(RunPod 전용).

### 경로 대응표 (작업 4)

| 용도 | 이전 | 이후 | 설정 키 / paths 함수 |
| --- | --- | --- | --- |
| 원본 JSON | data/krx_raw/<snapshot>/ | 변경 없음 | krx.cache_dir / krx_cache_dir() |
| 가격·벤치마크 | data/raw/{index}/prices.parquet | 변경 없음 | data.raw_dir, data.price_file / price_file(index) |
| 유니버스 | data/universe/constituents_*.parquet | 변경 없음 | data.universe_dir / constituents_file(index, variant) |
| 매니페스트 | data/MANIFEST.json | 변경 없음 | A_data_prepare.manifest (root/data 고정) |
| 로그 | logs/ | 변경 없음 | data.logs_dir / logs_dir() |
| 기준 데이터 (Stage 1) | — | data/A_prepared/{name}.parquet | data.prepared_dir / prepared_path(name) |
| 예측 | data/predictions/{run_id}/ | **data/B_predictions/{run_id}/** | data.predictions_dir / predictions_dir, prediction_file, manifest_file |
| 시그널 | — | data/C_signals/{run_id}/signals.parquet | data.signals_dir / signals_path(run_id) |
| 비중 | — | data/D_weights/{run_id}/{strategy}.parquet | data.weights_dir / weights_path(run_id, strategy) |
| 백테스트 | results/{run_id}/{strategy} | data/E_backtest/{run_id}/{engine}/{strategy}/ | data.backtest_dir / backtest_dir(run_id, engine, strategy) |
| 지표 | — | data/F_metrics/{run_id}/ | data.metrics_dir / metrics_dir(run_id) |
| 리포트 | — | reports/{run_id}/ | data.reports_dir / report_dir(run_id) |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest                                   33 passed (기존 22 + test_common.py 11)
python -m A_data_prepare.run build benchmark sanity --root <scratch>
                                         build 29s, benchmark 4s, sanity 2s. 하드 게이트 전부 PASS
                                         (FLUC_RT 일치 100%, 지수 재구성 corr kospi 1.0000 / kosdaq 0.9999, 캘린더 동일, ETF corr 0.983 / 0.945)
                                         산출물 27개 중 26개가 data/MANIFEST.json의 sha256과 바이트 단위 동일.
                                         다른 것은 raw/BUILD_METADATA.json 하나 (built_at_utc, code_commit, config_hash, universe_variants 키만 다름)
python -m A_data_prepare.manifest --verify   OK: 3665 files match (실제 data/는 건드리지 않았음)
python -m B_model_infer.run_inference --run-id stage0_dummy_check --backend dummy --start 2024-07-01 --end 2024-07-01
                                         data/B_predictions/stage0_dummy_check/as_of=2024-07-01.parquet (968 종목 × 5 스텝 × 20 샘플 = 96,800행) + manifest.json
                                         validate_predictions(as_of, horizon=5) 통과. manifest: profile base, lookback 400, pred_len 5, step 5, sample_count 20, skipped 48 (nan_in_window)
python scripts/probes/stage0_price_basis.py   아래 "[확인 필요]" 절
```

재빌드는 실제 data/raw·data/universe를 덮어쓰지 않기 위해 스크래치 루트(data/krx_raw만 심볼릭 링크)에서 했다. 매니페스트가 BUILD_METADATA.json(실행 시각 포함)까지 해시하므로 같은 자리에 다시 빌드하면 `--verify`는 그 파일 하나로 항상 FAIL이 난다. 그래서 "같은 파일이 나온다"는 산출물별 sha256 비교로 확인했다.

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] pytest 전체 통과 (tests/test_data_prepare.py 14, tests/test_no_lookahead.py 8, tests/test_common.py 11)
- [x] 기존 수집 스크립트를 새 경로에서 실행해도 같은 파일이 나온다 — prices.parquet 2개, benchmark, adjustment_events, validation.json, residual_big_moves, diag 4개, sanity_report.json, constituents 6개, coverage 6개 모두 sha256 동일. BUILD_METADATA.json만 실행 시각·커밋·설정 해시가 달라 불일치(의도된 메타데이터)
- [x] `python -m B_model_infer.run_inference --backend dummy`가 data/B_predictions/ 아래에 validate_predictions를 통과하는 파일과 manifest.json을 쓴다 (2024-07-01 하루치)
- [x] tests/test_common.py — config_hash 키 순서 무관 / require null 오류 / validate_* 5종 거부 / paths run_id별 비겹침
- [ ] (부분) 코드 폴더 git mv — 복사까지만 됨. 원본 삭제는 사용자 작업 (아래 1)

## [확인 필요] 항목의 probe 결과

Stage 0에는 `[확인 필요]` 표시가 없지만 "사용자 확인: 추론 입력의 가격 기준이 실제 코드와 일치하는지"를 실제 데이터로 확인하는 probe를 두었다 (scripts/probes/stage0_price_basis.py, 읽기 전용).

| as_of | 유니버스 | 윈도우 생성 | 마지막 종가 ≠ as_of 원주가 | as_of 이후 행 | 윈도우 안 adj_factor 변화 종목 | 그 종목들의 max \|Δlog close\| |
| --- | --- | --- | --- | --- | --- | --- |
| 2024-07-01 | 1016 | 968 | 0 | 0 | 108 | 0.3566 |
| 2024-12-26 | 918 | 860 | 0 | 0 | 93 | 0.3567 |
| 2025-06-30 | 1093 | 1028 | 0 | 0 | 100 | 0.3567 |

- 결론: 예측 입력은 **as_of 원주가 스케일**이다(모든 윈도우에서 마지막 종가 = as_of 원주가, 미래 행 0). 윈도우 안에 조정계수 변화가 있는 약 100종목에서도 rebase 후 일간 변화의 최대치는 0.357 = log(1/0.7)로 하한가(−30%) 하루에 해당하며, 2:1 분할이 남았다면 보였을 0.69는 없다. 즉 부록 A의 설명이 코드와 일치하고 `data.price_basis`의 답은 raw다(설정은 규칙대로 null로 두었음).
- 부수 발견: as_of마다 48 ~ 65종목(유니버스의 5 ~ 6%)이 `nan_in_window`로 추론에서 빠진다. 전부 lookback 400봉 안에 거래정지일(open/high/low NaN)이 있는 종목이고, 그중 30 ~ 43종목은 윈도우 안에 조정계수 변화도 있다(분할·감자 전후의 정지). 유니버스 필터는 as_of 당일 정지만 보고, build_batch는 과거 400봉 중 하루라도 NaN이면 종목 전체를 뺀다. "질문" 1 참고.

## 명세서와 달라진 점과 이유

1. **git mv 대신 복사.** 이 환경의 실행 권한 분류기가 `git mv`·`git rm`·`rmdir`(디렉터리 이동·삭제)를 "복구 불가 삭제"로 막았다. 그래서 data_prepare/ → A_data_prepare/, infer/ → B_model_infer/는 복사본을 만들고 복사본만 수정했다. **원본 data_prepare/, infer/와 빈 data/predictions/.gitkeep은 아직 있다.** 삭제와 커밋은 사용자가 한다 ("사용자 확인 요청" 1). 삭제 전까지 pytest는 tests/만 수집하므로 영향이 없지만, 원본 패키지는 옛 설정 키(run.*, model.*)를 읽어 더 이상 실행되지 않는다.
2. **config 뼈대의 `model:` 섹션 유지.** spec 뼈대에는 모델 이름·토크나이저·revision·kronos_repo·device·max_context·batch_size·top_k가 없다. KronosBackend가 필요로 하므로 `model:`에 그대로 두고, 프로필별 샘플링 값(lookback, pred_len, step, T, top_p, sample_count)만 `infer.profiles`로 옮겼다. seed는 `project.seed`(42) 하나로 통일(이전 model.seed 0).
3. **run.*·model.lookback·model.horizon·benchmark.index_tickers 키 제거 → A_data_prepare가 새 키를 읽는다.** spec은 "A가 읽는 키는 바꾸지 않는다"고 하면서 krx.*·universe.*·data.* 네 가지만 적었지만, 실제 run.py·sanity.py는 run.start/end, model.lookback/horizon, benchmark.index_tickers도 읽었다. 두 벌로 두면 같은 값이 두 곳에 생겨 spec 규칙 9("설정은 하나")에 어긋나므로 A 쪽 읽기 코드를 `period.start/end`, `infer.profiles.<default_profile>.lookback/pred_len`, `data.index_tickers`로 바꿨다(run.py 4곳, sanity.py 2곳, `base_profile(cfg)` 헬퍼 추가). 로직은 그대로이며 재빌드 산출물이 동일함을 해시로 확인했다.
4. **A_data_prepare/run.py의 config_hash·git_commit을 common으로 교체.** run.py가 자체 정의하던 config_hash(YAML 기반)와 git_commit을 지우고 common.config.config_hash(JSON 기반)와 common.meta.git_commit_hash를 쓴다. 중복 제거 목적이며, 그 결과 BUILD_METADATA.json의 config_hash 알고리즘이 달라졌다(이전 값과 비교 불가; 데이터 산출물에는 영향 없음).
5. **B_model_infer/run_inference.py가 Stage 2 작업 1의 일부를 앞당겼다.** `run.*`·`model.lookback` 등이 없어졌으므로 `period.*`와 `infer.profiles.<infer.default_profile>`에서 읽게 했고(`profile_cfg(cfg)`), manifest에 `profile`·`universe_variant`를 기록한다. `--profile` 플래그와 paper 프로필 실행은 Stage 2에서 한다. CLI 플래그(--lookback 등)는 default 프로필 값을 덮어쓴다. git_commit·write_manifest·atomic_parquet는 common/meta.py로 옮기고 같은 이름의 alias를 남겼다.
6. **universe.name 대신 기존 universe.variant 유지.** Paths.constituents_file이 universe.variant를 읽고 data_pipeline.md도 variant로 설명하므로 이름을 바꾸지 않았다(spec의 universe.name ≡ universe.variant). 값은 기존 `base`를 유지했고 `[사용자]` 표시만 달았다. `universe.top_n_mktcap: null` 추가.
7. **universe.markets 값 변경 (KR → KOSPI/KOSDAQ).** 새 설계의 market은 거래소(prices.market, sell_tax_table.market)이므로 index → market 매핑 값을 거래소 이름으로 바꿨다. common/data.load_prices는 이 값으로 market 컬럼을 채우고, 매핑에 없는 index에는 US 기본값을 넣던 `backtest.default_market` 분기를 없앴다. data_prepare 산출물은 이 키를 쓰지 않는다(BUILD_METADATA의 universe_variants 기록에만 남음).
8. **data.index_tickers를 null이 아니라 기존 값으로 채웠다.** spec은 `[사용자] (데이터가 있을 때)`로 표시했지만 벤치마크 데이터가 이미 있고 기존 benchmark.index_tickers 값(kospi: KOSPI, kosdaq: KOSDAQ)이 있어 옮겨 왔다("기존 설정 값이 있으면 옮겨 온다" 규칙 우선).
9. **pytest 설정을 pyproject.toml에서 pytest.ini로 옮겼다** (spec은 pytest.ini를 요구; 두 곳에 두지 않음).
10. **매니페스트 게이트의 확인 방법.** 위 "실행한 명령" 설명대로 스크래치 루트 재빌드 + sha256 비교.
11. **문서 수정.** README.md 전체(새 구조·명령), docs/data_pipeline.md의 실행 명령 줄·코드 경로 줄(내용 불변). docs/outline.md·spec.md는 건드리지 않았다.
12. **의존성 추가 없음.** requirements.txt는 pyproject의 dependencies + pytest를 현재 설치 버전으로 고정한 것이다.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **원본 폴더 삭제와 커밋** (에이전트 권한으로 불가). 아래를 실행하면 git이 rename으로 인식한다:
   ```bash
   git rm -r -q data_prepare infer data/predictions/.gitkeep
   git add -A A_data_prepare B_model_infer C_signal D_strategy E_backtest F_evaluate G_report common configs scripts tests docs/stage_reports docs/spec.md docs/data_pipeline.md README.md .gitignore pyproject.toml pytest.ini requirements.txt
   git commit -m "stage0: A_/B_ 패키지 이름 변경, common 공용 모듈·설정 뼈대·테스트 환경"
   ```
   사전 정리의 staged 삭제(backtest/, scripts/*.py, results/, tests/test_engine_sanity.py, tests/test_strategies.py)와 docs/outline.md 수정도 같은 커밋에 들어간다. docs/ 아래 논문 PDF는 11 MB다. 커밋하지 않으려면 `git add -A` 대신 위처럼 경로를 지정하거나 .gitignore에 추가하세요.
2. **경로 대응표**(위)와 **레포 현황 조사**(위)가 맞는지.
3. **추론 입력의 가격 기준**: probe 표와 결론(raw). 직접 재실행: `python scripts/probes/stage0_price_basis.py` (약 30초, 읽기 전용).
4. configs/base.yaml의 주석을 한 번 훑어 옮겨 온 값·출처가 맞는지. 특히 `universe.markets`(KOSPI/KOSDAQ), `data.index_tickers`, `costs.sell_tax_table: []`(이전 0.0018은 주석으로만 남김).
5. data/B_predictions/stage0_dummy_check/ 는 게이트 확인용 하루치 더미 예측이다(gitignore 대상). 지워도 된다.

## 질문과 미결정 사항

1. **거래정지 이력이 있는 종목의 추론 제외.** build_batch는 400봉 윈도우 안에 NaN(정지일의 open/high/low)이 하루라도 있으면 종목을 뺀다. as_of마다 유니버스의 5 ~ 6%(48 ~ 65종목)가 이 이유로 예측이 없고, 분할·감자 전후 정지 종목이 다수 포함된다. 선택지: (a) 그대로 두고 C_signal·D_strategy에서 "예측 없는 종목은 현금"으로 처리(현재 동작, 벤치마크 전략은 유니버스 전체를 쓰므로 Kronos 전략과 종목 집합이 어긋남), (b) Stage 2(또는 Stage 6 전 RunPod 추론 전)에 build_batch가 정지일 OHL을 종가로 채우고 거래량 0으로 두는 옵션을 설정(`infer.halted_fill`)으로 추가, (c) 유니버스 필터에 "최근 lookback 안 정지 없음"을 추가해 두 쪽 종목 집합을 맞춤. RunPod 추론 전에 정해야 하므로 Stage 2 선행 결정으로 올리는 것을 제안한다.
2. **market 키 중복.** `data.markets`(spec 뼈대), `krx.markets`(수집), `universe.markets`(index → market)가 같은 정보를 세 번 적는다. 수집 키는 바꾸지 않는다는 규칙 때문에 그대로 두었다. 이후 Stage에서 `data.markets`만 참조하고 나머지는 수집 전용으로 간주할지 확인 바란다.
3. **data.price_basis.** probe 결과는 raw다. 규칙대로 null로 두었으니 Stage 2 선행 결정에서 `raw`로 채워 주세요.
4. **universe.variant.** 기존 값 base를 유지했다. Stage 1 선행 결정(base / liq5 / clean)과 같은지 확인 바란다.
5. **requirements.txt 버전.** macOS에 설치된 버전으로 고정했다. Windows 백테스트 환경에서 같은 버전을 쓸 수 없으면 그쪽에서 `pip freeze`로 다시 고정한다.
6. **A_data_prepare 로그 이름.** 로거 이름과 로그 파일(logs/data_prepare.log)은 그대로 두었다. 바꾸려면 알려 주세요.

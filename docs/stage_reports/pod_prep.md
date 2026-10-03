# 부록 C-1 보고서 (RunPod 로컬 준비)

2026-10-03. 범위: docs/spec.md 부록 C "C-1. 로컬 준비"(부록 D 원칙·선행 결정 포함) + D-5(추론 유니버스 liq5) 적용. Pod 생성·업로드는 하지 않았다. 같은 커밋에 Stage 5 후속(D-16·D-17, docs/stage_reports/stage5.md 끝 절)이 들어 있다. Stage 6은 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

| 파일 | 설명 |
| --- | --- |
| B_model_infer/env_info.py | `collect_env(device, dtype)` → gpu_name, gpu_count, driver_version, cuda_version, torch_version, python_version, dtype, hostname, pod_id(RUNPOD_POD_ID), platform. CPU에서는 GPU 항목 None. `python -m B_model_infer.env_info`로 출력 |
| B_model_infer/run_inference.py | manifest 보강: `env`(시작 시 collect_env), `env_by_date[as_of]`(gpu_name, driver_version, hostname), `elapsed_by_date[as_of]`(초), `sessions`(호출마다 started_at·finished_at·hostname·gpu·pod_id·n_written: 다른 호스트에서 재개된 날짜 구분), `n_files_present`, `dtype`, `universe_variant`(추론, D-5)·`backtest_universe_variant`. `--backend kronos`는 `model.revision`·`model.tokenizer_revision`이 null이면 ConfigError(`--allow-unpinned`는 스모크 전용, 해석된 sha는 manifest에 기록), `model.dtype != float32` 거부, `model.batch_size` null이면 `--batch-size` 요구. 스모크용 `--max-tickers N`(manifest `smoke: true`) |
| B_model_infer/backends.py | KronosBackend에 `tokenizer_revision` 분리, `resolved_revision`이 모델·토크나이저 sha를 각각 기록 |
| B_model_infer/pod_bundle.py | `pack --run-id [--profile] [--allow-dirty]` → data/pod_bundles/{run_id}.tar.gz: `git archive HEAD`(data/·docs/·reports/ 제외; 추적 파일 수정분이 있으면 거부, `--allow-dirty`면 inputs.sha256.json에 dirty 목록 기록) + 작업본 configs/base.yaml·requirements.txt·requirements-infer.txt + data/raw/{kospi,kosdaq}/prices.parquet + 프로필 universe_variant의 constituents(liq5) + inputs.sha256.json(파일명·바이트·sha256·코드 커밋). data/krx_raw와 단계 산출물은 금지 목록. `verify --dir` 불일치 시 exit 1 |
| B_model_infer/checksum.py | `write|verify --run-id` → data/B_predictions/{run_id}/checksums.json (as_of 파일마다 바이트·sha256, 개수, 코드 커밋). 개수·해시 불일치 시 exit 1 |
| B_model_infer/pod/setup_pod.sh | 번들 풀기 → `pod_bundle verify` → torch 버전이 requirements-infer.txt와 다르면 중단 → pip install → Kronos 코드 clone·`model.kronos_repo_commit` 체크아웃(null이면 중단) → 모델·토크나이저 `snapshot_download`(revision null이면 중단) → nvidia-smi + collect_env 출력 |
| requirements-infer.txt | `-r requirements.txt` + torch==2.14.1, huggingface_hub==2.1.1, einops==0.8.2, safetensors==0.8.0, tqdm==4.66.4 (로컬 스모크에 쓴 버전, D-8) |
| configs/base.yaml | `model.revision`·`tokenizer_revision`·`kronos_repo_commit`: null([사용자]), `model.dtype: float32`, `model.batch_size: null`(Pod probe 후 기입), `infer.profiles.{base,paper}.universe_variant: liq5`(D-5), `data.bundles_dir` |
| common/universe.py, common/paths.py | `load_constituents(..., variant=)`, `Paths.bundles_dir/bundle_path/checksums_file` |
| B_model_infer/make_fake_predictions.py | 프로필 universe_variant(liq5)로 유니버스를 읽는다(D-5). 가짜 예측 4종을 liq5로 다시 생성했다 |
| tests/test_pod_tools.py | 5개 (아래) |
| .gitignore | Kronos/ (코드 클론), data/pod_bundles/ |
| README.md | C-1 ~ C-2 명령 순서 |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pip install torch huggingface_hub einops safetensors tqdm      torch 2.14.1 (CPU), huggingface_hub 2.1.1, einops 0.8.2, safetensors 0.8.0
git clone https://github.com/shiyu-coder/Kronos ./Kronos      HEAD 67b630e67f6a18c9e9be918d9b4337c960db1e9a
python -m B_model_infer.run_inference --backend kronos --run-id smoke_cpu --device cpu --batch-size 4 --sample-count 2 \
       --max-tickers 5 --allow-unpinned --start 2024-07-01 --end 2024-07-01
       총 2분 56초(모델·토크나이저 다운로드 포함), 추론 5.4초. 유니버스 앞 5종목 중 4종목 예측(1종목 nan_in_window), 40행 = 4 × 5스텝 × 2샘플.
       validate_predictions(as_of, H 5) 통과. hf_revision_resolved: model 2b554741eca47781b64468546e77fef3e85130e6,
       tokenizer 0e0117387f39004a9016484a186a908917e22426. manifest env: torch 2.14.1, python 3.12.4, gpu None, macOS arm64.
       sessions 1건(started/finished), elapsed_by_date {2024-07-01: 5.37}, env_by_date 기록.
python -m B_model_infer.checksum write --run-id smoke_cpu      1 file, 8,055 bytes;  verify -> OK
python -m B_model_infer.pod_bundle pack --run-id kronos_base_v1 --allow-dirty
       data/pod_bundles/kronos_base_v1.tar.gz 39.6 MB (내용 82 파일 45.7 MB: prices kosdaq 29.5 MB·kospi 15.5 MB, constituents liq5 2개, 코드).
       풀어서 verify -> OK. krx_raw 없음. (--allow-dirty: 작업 트리에 이 커밋 전 수정분이 있어 dirty로 기록됨)
pytest tests/test_pod_tools.py                               5 passed
```

스모크 예측의 가격 수준(as_of 원주가 대비 5스텝 평균 비율): 000020 1.11, 000070 1.00, 000080 0.99, 000100 0.80 — 샘플 2개라 분산이 크지만 모두 as_of 원주가 스케일이다(분할 이력 종목 확인은 C-2 Pod probe 항목).

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] pack 결과에 허용 목록 밖의 파일이 없고 data/krx_raw가 없다 (테스트: 합성 git 레포에서 pack → 코드·configs·requirements·prices·liq5 constituents·inputs.sha256.json만, base 변형 constituents와 krx_raw 없음)
- [x] 번들 파일 하나를 1바이트 바꾸면 verify가 실패한다 (추가: 미등록 parquet도 실패, dirty 트리는 거부·--allow-dirty면 기록)
- [x] checksum write 후 verify 통과, 파일을 바꾸거나 지우면 실패 (+ 없는 run_id 실패)
- [x] collect_env가 CPU에서 필수 키를 모두 돌려준다 (GPU 항목 None)
- [x] (선택 2 대비) aggregate가 sample_id 앞에서부터 n_samples개만 쓴다 (sample_count 50 → 앞 20개, 뒤 샘플을 바꿔도 불변)
- [x] CPU 스모크 테스트: KronosBackend device=cpu, 리밸런싱일 1 × 종목 5(4 예측) × sample_count 2, validate_predictions 통과, manifest에 환경 정보. 결과 폴더(data/B_predictions/smoke_cpu)는 커밋하지 않는다

## [확인 필요] 항목의 probe 결과

부록 C-1에 `[확인 필요]` 표시는 없다. Pod probe(C-2 3번)는 사용자 작업이다.

## 명세서와 달라진 점과 이유

1. **git archive에서 data/·docs/·reports/를 뺀다.** 레포가 data/ 아래 메타데이터(MANIFEST.json, BUILD_METADATA.json, krx_raw PULL_METADATA 등)를 추적하고 있어 그대로 두면 금지 목록(krx_raw)에 걸린다. 데이터는 명시 목록(prices, 프로필 유니버스)으로만 넣는다. 나머지 코드 패키지(A~G, tests, scripts)는 작아서 그대로 둔다(45.7 MB 중 코드 0.5 MB).
2. **`--allow-dirty`**: 명세는 "깨끗하지 않으면 오류"다. 기본은 오류이고, 이번 보고서용 pack은 커밋 전이라 `--allow-dirty`로 만들었다(inputs.sha256.json에 dirty 파일 목록 기록). 실제 Pod 업로드용 번들은 커밋 뒤 플래그 없이 만든다.
3. **스모크용 옵션 2개**: `--max-tickers`(종목 5개 제한)와 `--allow-unpinned`(revision null 허용). 둘 다 manifest에 흔적(`smoke: true`, `hf_revision_requested: null`)이 남고 실제 실행에서는 쓰지 않는다. D-12의 "spec에 없는 추론 옵션 금지"에 걸리지만 C-1 6번(종목 5개 스모크)을 하려면 필요했다.
4. **부록 D의 `infer:` 예시 키는 D-7대로 `model:`에 대응**: model.revision, model.tokenizer_revision, model.kronos_repo_commit, model.dtype, model.batch_size. 프로필 아래 `universe_variant`(D-5).
5. **manifest 세션 기록**: 명세의 "전체 시작·종료 시각"을 `sessions` 목록(호출마다)으로 넓혔다. 재개된 실행이 어느 호스트에서 몇 날짜를 썼는지 보인다.
6. **requirements-infer.txt의 huggingface_hub 2.1.1**: Kronos 레포 requirements는 0.33.1을 적지만 2.1.1에서 from_pretrained·model_info가 동작했다(스모크). Pod 템플릿의 torch 버전이 2.14.1이 아니면 setup_pod.sh가 멈추므로 템플릿에 맞춰 핀을 바꾼다(C-2 1번).
7. **가짜 예측 재생성(D-5)**: liq5 유니버스로 네 run_id를 다시 만들었다. 시그널 행은 base ∩ 예측이므로 종목 수는 이전과 거의 같고, 예측 파일은 약 1.3배 커졌다. 수치는 stage5.md 후속 절.

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **핀 값 기입**(configs/base.yaml, null 유지 중): 스모크가 해석한 값은 `model.revision: 2b554741eca47781b64468546e77fef3e85130e6`, `model.tokenizer_revision: 0e0117387f39004a9016484a186a908917e22426`, `model.kronos_repo_commit: 67b630e67f6a18c9e9be918d9b4337c960db1e9a`(2026-10-03 main). 이 값을 쓸지 확인 후 기입해 주세요. 기입 전에는 `--backend kronos`가 멈춘다.
2. **sample_count**(선택 2): base 프로필 20 유지인지 상향(예 50)인지. 상향하면 생성 시간이 비례해 늘고, 시그널은 앞 20개만 쓴다(D-11).
3. **Pod 템플릿**: requirements-infer.txt의 torch 2.14.1과 맞는 PyTorch 템플릿을 고르거나, 템플릿의 torch 버전으로 핀을 바꾼다.
4. 번들 내용 목록(data/pod_bundles/kronos_base_v1.tar.gz 안 inputs.sha256.json)과 setup_pod.sh의 단계 순서.

## 질문과 미결정 사항

1. **C-2 3번 Pod probe 뒤 기입할 값**: `model.batch_size`, 날짜당 소요 시간, 결정성 여부(같은 날짜 두 번 → 비트 동일?), 분할 이력 종목의 예측 스케일. probe run_id는 `probe_<날짜>`로 본 run_id와 분리.
2. **backtest.delist_policy**: 여전히 null. 이번 재생성 실행에도 `--set backtest.delist_policy=last_close`를 썼다.
3. 로컬에 설치한 torch(약 100 MB)와 ./Kronos 클론은 gitignore 대상이며 requirements.txt(백테스트용)에는 들어가지 않는다(D-8).

# RunPod에서 추론 돌리기 (docs/spec.md 부록 C)

> **Claude 세션은 이 문서를 먼저 읽고 §0의 명령으로만 pod와 결과 브랜치를 다룹니다.**

실제 Kronos 예측(`kronos_base_v1`, `kronos_paper_v1`)을 RunPod Secure Cloud의 GPU pod에서 만들고 로컬로 가져오는 절차다.
무엇이 어느 길로 움직이는지가 전부다.

| 대상 | 크기 | 경로 |
| --- | --- | --- |
| 코드, 설정 | 작음 | GitHub: 로컬 `push-code` → pod `git clone` / 새 RUN_ID는 최신 origin/main에서 시작 |
| 입력 데이터 (prices, liq5 유니버스) | 45 MB | SSH: 로컬 `upload` → pod `/workspace/inputs` → 커밋된 `RunPod/inputs.sha256.json`과 sha256 대조 |
| 예측 parquet | base 약 250 MB, paper 약 1.3 GB | pod의 **네트워크 볼륨**(`/workspace`)에 쓰고, SSH(rsync)로 로컬 회수 → `checksum verify`. GitHub에 올리지 않는다 |
| 실행 메타데이터 (manifest.json, checksums.json, cloud_run.json, requirements.lock.txt) | 작음 | GitHub: pod가 `results/<RUN_ID>` 브랜치로 push → 로컬 `merge`가 main에 병합 |

다른 클라우드 저장소는 쓰지 않는다. 네트워크 볼륨은 pod가 사라져도 남는 저장소이고, 로컬로 옮기는 수단은 SSH다.

## 0. 실행 한 번의 루프

```
로컬 main                       pod (/workspace = 네트워크 볼륨)            로컬 main
──────────────                  ─────────────────────────────────           ─────────────────────────
① push-code "메시지"   ──────▶  git clone (첫 pod만)
② upload <host> <port> ──────▶  /workspace/inputs
                                ③ RUN_ID=... bash RunPod/runpod.sh
                                   설치 → 입력 대조 → GPU 스모크 → 추론
                                   → 구조 검사 → checksum write
                                   → 메타데이터를 results/<RUN_ID>로 push   ──▶ ④ fetch <RUN_ID>  (rsync + checksum verify)
                                   (pod는 스스로 종료하지 않는다)               ⑤ merge <RUN_ID>  (메타데이터만 main으로)
                                                                               ⑥ terminate <RUN_ID>
```

| 단계 | 어디서 | 명령 | 하는 일 |
| --- | --- | --- | --- |
| ① | 로컬 | `bash RunPod/local.sh push-code "메시지"` | main에 커밋하고 push (`pull --rebase` 먼저) |
| ② | 로컬 | `bash RunPod/local.sh upload <host> <port>` | 로컬 데이터가 `RunPod/inputs.sha256.json`과 같은지 확인하고 pod의 `/workspace/inputs`로 보냄. 볼륨에 남으므로 볼륨당 한 번 |
| ③ | pod | `RUN_ID=kronos_base_v1 bash RunPod/runpod.sh` | 아래 §1.3. **pod에서는 코드를 고치지 않음** |
| ④ | 로컬 | `bash RunPod/local.sh fetch kronos_base_v1` | pod 주소를 결과 브랜치의 cloud_run.json에서 읽어 `data/B_predictions/<RUN_ID>/`를 rsync하고 `checksum verify` |
| ⑤a | 로컬 | `bash RunPod/local.sh merge kronos_base_v1` | 남길 실행: 메타데이터를 main에 병합·push, 원격 결과 브랜치 삭제. 로컬 verify가 통과해야 함 |
| ⑤b | 로컬 | `bash RunPod/local.sh drop probe_20240701` | 버릴 실행(probe 등): 원격 결과 브랜치만 삭제 |
| ⑥ | 로컬 | `bash RunPod/local.sh terminate kronos_base_v1` | pod 종료. 로컬 verify가 통과하지 않으면 거부. 네트워크 볼륨은 남음 |
| 확인 | 로컬 | `status` / `list` / `ssh <RUN_ID>` / `same <A> <B>` | 상태, 결과 브랜치 목록, pod 셸, 두 실행의 파일이 비트 단위로 같은지 |

**규칙**

1. 코드는 로컬 main에서만 고치고 `push-code`로 올린다. pod는 새 RUN_ID를 시작할 때 최신 origin/main을 받는다.
2. RUN_ID 하나는 예측 빈티지 하나다. 덮어쓰지 않는다. 같은 RUN_ID로 다시 실행하면 "끊긴 실행 이어 하기"이고(이미 있는 날짜는 건너뜀, 코드는 그 브랜치의 것 그대로), 다시 생성하려면 새 RUN_ID를 쓴다.
3. pod는 결과만 만든다. pod의 커밋은 `results/<RUN_ID>` 브랜치에만 가고 메타데이터 파일 네 개만 담는다.
4. 로컬 `checksum verify`가 통과하기 전에는 pod를 종료하지 않는다(`terminate`가 강제한다).
5. Stage 6 전에는 예측의 성과(IC, 수익률)를 보지 않는다. 여기서는 구조 검증만 한다.

## 1. Pod 배포

### 1.1 사양

| 항목 | 값 | 어디서 정해지나 |
| --- | --- | --- |
| 클라우드 | **Secure Cloud**, On-Demand | 배포 화면. pod에 GitHub 토큰을 두므로 Community Cloud는 쓰지 않는다 (D-18) |
| GPU | RTX 4090 24 GB × 1 | 배포 화면. 같은 RUN_ID 안에서 GPU 종류를 섞지 않는다 |
| 네트워크 볼륨 | 50 GB, `/workspace`에 마운트 | Storage → Network Volume. 볼륨은 데이터센터에 묶이므로 4090 재고가 있는 곳에 만든다 |
| Python | 3.12 | `setup_runpod.sh`가 `/workspace/venv`에 설치 (이미지에 없으면 `uv`로 받음) |
| torch 등 | `requirements-infer.txt` (torch 2.14.1, D-8) | 로컬 CPU 스모크가 돈 버전 그대로 |
| Kronos 코드, 모델 | `configs/base.yaml`의 `model.kronos_repo_commit`, `model.revision`, `model.tokenizer_revision` | null이면 `setup_runpod.sh`가 멈춘다 |

- **이미지는 무엇이든 된다.** 이미지에 깔린 torch는 쓰지 않는다. git, curl, nvidia-smi, SSH(`PUBLIC_KEY` 처리)가 있는 RunPod 공식 이미지면 된다.
- 볼륨 용도: venv 약 8 GB + HF 캐시 + 예측(base 0.25 GB, paper 1.3 GB, probe) + 입력 45 MB.

### 1.2 배포 화면에 넣는 환경변수

| 이름 | 값 | 스크립트가 하는 일 | 없으면 |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | **이 레포**에 Contents: Read and write 권한이 있는 fine-grained PAT | clone(비공개 레포), 결과 브랜치 fetch, 메타데이터 push | `runpod.sh`가 시작을 거부 (`ALLOW_NO_PUSH=1`이면 진행, `fetch`에 host·port를 직접 줘야 함) |
| `PUBLIC_KEY` | 내 SSH 공개키 한 줄 | SSH 접속. `upload`·`fetch`가 이 키로 들어간다 | 데이터를 옮길 수 없다 |
| `RUNPOD_USER_API_KEY` | RunPod API 키 | **pod에서는 쓰지 않는다.** 템플릿에 있어도 무해. 로컬 `terminate`가 로컬 환경변수 또는 `RunPod/.env`에서 읽는다 | 웹에서 직접 종료 |

`RUNPOD_POD_ID`, `RUNPOD_PUBLIC_IP`, `RUNPOD_TCP_PORT_22`는 RunPod이 넣는다(TCP 22를 expose해야 뒤의 둘이 생긴다).
SSH 세션은 컨테이너 환경변수를 상속하지 않으므로 스크립트가 PID 1의 환경에서 읽는다.

MMDL-MMMU용으로 만든 `GITHUB_TOKEN`은 그 레포에만 걸려 있다. 토큰의 Repository access에 `jinyoung924/Kronos-investing`을 추가하거나 새 토큰을 만든다.

### 1.3 pod 안에서 실행

```bash
# 첫 pod (볼륨이 비어 있을 때) 한 번. 토큰은 PID 1 환경에서 꺼낸다
TOKEN=$(tr '\0' '\n' < /proc/1/environ | sed -n 's/^GITHUB_TOKEN=//p')
git clone https://x-access-token:${TOKEN}@github.com/jinyoung924/Kronos-investing.git /workspace/Kronos-investing
git -C /workspace/Kronos-investing remote set-url origin https://github.com/jinyoung924/Kronos-investing.git   # 토큰을 .git/config에 남기지 않는다

cd /workspace/Kronos-investing
apt-get install -y -qq tmux >/dev/null; tmux new -s infer        # SSH가 끊겨도 실행이 유지된다

# probe: 첫 리밸런싱일 하나. batch_size를 바꿔 가며 처리량을 본다
RUN_ID=probe_20240701   INFER_ARGS="--start 2024-07-01 --end 2024-07-01 --batch-size 256" bash RunPod/runpod.sh
RUN_ID=probe_20240701_b INFER_ARGS="--start 2024-07-01 --end 2024-07-01 --batch-size 256" SKIP_SMOKE=1 bash RunPod/runpod.sh   # 결정성 확인용

# 본 실행 (model.batch_size를 로컬에서 기입하고 push-code한 뒤)
RUN_ID=kronos_base_v1 bash RunPod/runpod.sh
RUN_ID=kronos_paper_v1 PROFILE=paper bash RunPod/runpod.sh
```

`runpod.sh`가 하는 일: 환경변수 읽기 → 수정된 추적 파일이 있으면 거부 → `results/<RUN_ID>` 브랜치(새 RUN_ID면 최신 origin/main에서) →
push 권한 사전 검사 → `setup_runpod.sh` → cloud_run.json 기록·push → 입력 sha256 대조 → GPU 스모크(종목 5 × 샘플 2, run_id `smoke_gpu`) →
`RUN_CMD` → `verify_run`(파일 수, validate_predictions) → `checksum write` → 메타데이터 push.
어떤 경로로 끝나도 상태를 cloud_run.json에 적고 push한다. 실패하면 pod는 그대로 남으니 `logs/runpod_<RUN_ID>.log`를 본다.

### 1.4 환경변수 (스크립트 knob)

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `RUN_ID` | (필수) | 예측 폴더와 결과 브랜치 이름. `probe_<날짜>`, `kronos_base_v1`, `kronos_paper_v1` |
| `PROFILE` | `infer.default_profile` | 추론 프로필 (`base`, `paper`) |
| `INFER_ARGS` | 빈 값 | `run_inference`에 그대로 붙는 플래그 (`--batch-size`, `--start`, `--end`, `--sample-count`) |
| `RUN_CMD` | `python -m B_model_infer.run_inference --backend kronos --run-id $RUN_ID ...` | 실제로 돌릴 명령 |
| `SKIP_SMOKE` | `0` | `1`이면 GPU 스모크 생략 |
| `INPUTS_DIR` | `/workspace/inputs` | `upload`가 넣은 입력 위치 |
| `TORCH_INDEX_URL` | 빈 값 | torch 휠을 다른 인덱스에서 받아야 할 때 (§4) |
| `ALLOW_NO_PUSH` | `0` | `1`이면 `GITHUB_TOKEN` 없이 실행 (테스트용) |
| `VENV_DIR`, `HF_HOME` | `/workspace/venv`, `/workspace/hf` | 볼륨에 남는 위치 |

### 1.5 실행 중 생기는 파일 (pod의 `data/B_predictions/<RUN_ID>/`)

| 파일 | 내용 | GitHub | 로컬 회수 |
| --- | --- | --- | --- |
| `as_of=YYYY-MM-DD.parquet` | 원시 예측 샘플 | 안 감 | `fetch` (rsync) |
| `manifest.json` | 모델·추론 설정, revision, env, env\_by\_date, elapsed\_by\_date, skipped\_by\_date, sessions | 결과 브랜치 | `fetch`, `merge` |
| `checksums.json` | 파일별 바이트와 sha256 | 결과 브랜치 | `fetch`, `merge` |
| `cloud_run.json` | run\_id, `code_commit`, pod\_id, GPU, ssh\_host·ssh\_port, pred\_dir, 시작·종료, `status`(running/ok/failed), 세션 목록 | 결과 브랜치 | `fetch`, `merge` |
| `requirements.lock.txt` | 실제 설치된 패키지 | 결과 브랜치 | `fetch`, `merge` |
| `console.log` | 콘솔 출력 사본 (원본 `logs/runpod_<RUN_ID>.log`) | 안 감 | `fetch` |

코드 커밋은 cloud_run.json의 `code_commit`이 기준이다(재개 시 manifest.json의 `code_commit`은 결과 브랜치의 HEAD가 된다).

## 2. 파일

| 파일 | 어디서 실행 | 역할 |
| --- | --- | --- |
| `local.sh` | 로컬 | §0의 동작 전부 |
| `runpod.sh` | pod | 진입점 |
| `setup_runpod.sh` | pod | Python 3.12 venv, `requirements-infer.txt`, Kronos 코드 체크아웃, 모델·토크나이저 다운로드, torch 버전·CUDA 확인. 재실행 안전 |
| `push_meta.sh` | pod | 실행 폴더의 메타데이터 네 개만 커밋해 `results/<RUN_ID>`로 push |
| `inputs.sha256.json` | — | 입력 데이터의 sha256 목록(커밋됨). 데이터가 바뀌면 `python -m B_model_infer.pod_inputs write` 후 `push-code` |
| `.env` (gitignore) | 로컬 | 선택: `RUNPOD_USER_API_KEY=...`, `RUNPOD_SSH_KEY=~/.ssh/id_ed25519_runpod`, `PYTHON=python` |

관련 모듈: `B_model_infer/pod_inputs.py`(입력 목록 write·list·verify), `B_model_infer/verify_run.py`(구조 검사), `B_model_infer/checksum.py`(write·verify), `B_model_infer/env_info.py`.

## 3. 브랜치 규칙과 안전장치

```
main                ── push-code의 코드·문서 커밋 + merge가 만드는 "merge results/<RUN_ID>" 커밋
results/<RUN_ID>    ── pod가 만드는 "results(<RUN_ID>): ... metadata" 커밋만. data/B_predictions/<RUN_ID>/의 메타데이터 네 개만 추가
```

| 상황 | 막는 장치 |
| --- | --- |
| 결과 커밋에 코드나 parquet이 섞임 | `push_meta.sh`: `results/*` 브랜치가 아니면 거부, 정해진 네 파일만 stage. `runpod.sh`: 수정된 추적 파일이 있으면 실행 거부 |
| 입력 데이터가 로컬과 다름 | `upload`가 보내기 전에, `runpod.sh`가 실행 전에 커밋된 sha256 목록과 대조 |
| 토큰에 쓰기 권한이 없음 | `runpod.sh`가 시작할 때 `push --dry-run` |
| 회수 중 파일 손상·누락 | `fetch`의 `checksum verify`. 실패하면 `merge`와 `terminate`가 거부 |
| pod가 중간에 죽음 | 예측은 네트워크 볼륨에 남는다. 같은 볼륨으로 새 pod를 띄워 같은 명령을 재실행 |
| 두 pod가 같은 브랜치에 push | 거절 → fetch+rebase 재시도 5회 → 예비 브랜치 `results/<RUN_ID>-<host>-<시각>` |

## 4. 문제 해결

| 증상 | 원인 / 조치 |
| --- | --- |
| `FATAL: GITHUB_TOKEN cannot push` / 403 | 토큰에 이 레포의 Contents write가 없음. 켜져 있는 pod의 환경변수는 바뀌지 않으므로 `GITHUB_TOKEN=<새 토큰> RUN_ID=... bash RunPod/runpod.sh` |
| `FATAL: ... kronos_repo_commit is null` / `revision ... is null` | 로컬에서 `configs/base.yaml`에 핀 값을 기입하고 `push-code`. pod에서 새 RUN_ID로 실행하면 최신 main을 받는다 |
| `CUDA not available inside torch` | PyPI 기본 torch 휠의 CUDA가 호스트 드라이버보다 새것. `nvidia-smi`의 드라이버 버전을 보고 배포 화면 CUDA 필터를 올려 재배포하거나, `TORCH_INDEX_URL`로 맞는 휠 인덱스를 지정하고 `/workspace/venv`를 지운 뒤 재실행. 확인된 조합을 §1.1 표에 적는다 |
| `torch X installed but requirements-infer.txt pins Y` | `/workspace/venv` 삭제 후 재실행 |
| `input data is missing or differs` | 로컬에서 `bash RunPod/local.sh upload <host> <port>` |
| `tracked files are modified` (pod) | pod에서 코드를 고쳤음. `git checkout -- .` 후 로컬에서 고쳐 `push-code` |
| GPU 메모리 부족 | `INFER_ARGS="--batch-size <절반>"`으로 같은 RUN_ID 재실행 |
| `fetch`: `no ssh endpoint` | 결과 브랜치가 아직 없거나 TCP 22가 expose되지 않음. `fetch <RUN_ID> <host> <port>`로 직접 지정 |
| `fetch`: `checksum verify FAILED` | 실행이 아직 안 끝났거나(checksums.json 없음) 복사 중 끊김. 다시 `fetch` |
| 날짜 파일 손상 (`verify_run` 실패) | `checksum write` 전이면 해당 파일만 지우고 재실행. 그 뒤라면 새 RUN_ID로 처음부터 |

## 5. RunPod 템플릿 값

| 필드 | 값 |
| --- | --- |
| Template Name | `kronos-infer` |
| Template Type | `Pod` |
| Container Image | `runpod/pytorch:1.3.2-cu1290-torch2130-ubuntu2404` (MMDL-MMMU에서 쓰던 이미지. 이미지의 torch는 쓰지 않는다) |
| Container Start Command | (비움: 이미지 기본 `/start.sh`가 `PUBLIC_KEY`를 넣고 sshd를 띄운다) |
| Container Disk | `20` GB |
| Volume Mount Path | `/workspace` (네트워크 볼륨을 연결) |
| Expose TCP Ports | `22` |

Environment Variables: `GITHUB_TOKEN` = `{{ RUNPOD_SECRET_GITHUB_TOKEN }}`, `PUBLIC_KEY` = 공개키 한 줄. (`RUNPOD_USER_API_KEY`는 있어도 되고 없어도 된다.)

배포 시 선택: Secure Cloud, RTX 4090 × 1, On-Demand, Network Volume 연결, 템플릿 `kronos-infer`. 실행 시점의 시간당 요금을 `docs/stage_reports/pod_run.md`에 적는다.

## 6. Probe로 확정한 사항 (2026-10-04, RTX 4090 Secure Cloud, torch 2.14.1+cu130, 드라이버 580)

측정은 2024-07-01 하루치다. 전체 하루는 `probe_20240701`(1,204종목), 나머지는 앞 200종목(`--max-tickers 200`)으로 쟀다.

### 6.1 소요 시간과 비용

| 실행 | 실측 | 전체 추정 | $0.74/시간 기준 |
| --- | --- | --- | --- |
| base (`kronos_base_v1`): 49일, 20샘플, H 5, lookback 400 | 1,204종목 444초 (종목당 0.37초) | 약 5.6시간 (종목·날짜 54,971개) | 약 $4.2 |
| paper (`kronos_paper_v1`): 241일, 10샘플, H 10, lookback 90 | 196종목 18.2초 (종목당 0.093초) | 약 7.3시간 (종목·날짜 282,800개) | 약 $5.4 |
| 합계 | | 약 13시간 | 약 $9.6 |

- 종목·날짜 수는 같은 유니버스(liq5)로 만든 가짜 예측의 manifest에서 셌다.
- 처음 한 번의 환경 설치는 약 13분이다(네트워크 볼륨에 남는다). 대기 시간과 볼륨 요금은 별도다.

### 6.2 batch_size: 256으로 고정

| batch_size | base 200종목 | GPU 메모리 최대 | GPU 사용률 |
| --- | --- | --- | --- |
| 120 | 66.8초 | 7.4 GB | 90% |
| 256 | 68.7초 | 16.8 GB | 85% |
| 320 | 68.5초 | 22.0 GB | 85% |
| 400 | 66.7초 | 23.6 GB | 85% |
| 1024 이상 | 메모리 부족 | — | — |

- **배치를 키워도 빨라지지 않는다.** GPU가 이미 포화 상태다. paper 프로필도 250 → 4000에서 19.2초 → 18.3초로 차이가 없다.
- `model.batch_size: 256`으로 기입했다(전체 하루 probe에서 문제없던 값).
- **batch_size를 바꾸면 샘플 값이 달라진다**(같은 seed라도 난수가 배치 단위로 뽑힌다. 120과 256의 행 일치율 5%, 종목별 평균 예측의 상관 0.9999). 한 run_id 안에서, 그리고 재개할 때 batch_size를 바꾸지 않는다.

### 6.3 결정성

같은 설정(batch 256, 200종목)으로 4번 돌린 결과 파일의 sha256이 모두 같았다. 같은 GPU 종류·같은 환경에서는 비트 단위로 재현된다.
전체 하루치의 결정성은 `probe_20240701_b`를 돌려 `local.sh same`으로 확인한다(아직 하지 않음).

### 6.4 더 빠르게 하는 방법 검토

| 방법 | 이 환경에 적용 가능한가 | 결론 |
| --- | --- | --- |
| batch_size 키우기 | 가능 | 효과 없음 (§6.2) |
| vLLM | **불가** | vLLM은 Hugging Face 표준 구조의 텍스트 LLM을 서빙하는 엔진이다. Kronos는 자체 `nn.Module`(가격 토크나이저, s1/s2 이중 토큰 헤드, 시간 임베딩)이라 vLLM이 불러올 수 없다. vLLM의 이점(여러 요청의 연속 배칭)도 고정 길이 오프라인 배치에는 해당하지 않는다 |
| TF32 행렬곱 (`torch.backends.cuda.matmul.allow_tf32`) | 가능, 측정함 | 67초 → 51초(1.3배). 그러나 **결과가 달라진다**(행 일치율 0.1%, 중앙값 상대 차이 3e-5, 종목별 평균 예측 상관 0.99998). 절약이 약 $2라 쓰지 않는다 |
| bf16 / autocast | 가능 | 수치가 달라져 spec이 금지한다(`model.dtype: float32`). 측정하지 않음 |
| KV 캐시 (스텝마다 전체 문맥을 다시 계산하지 않기) | 코드 수정 필요, 미구현 | Kronos 공식 코드는 예측 스텝마다 문맥 전체를 다시 통과시킨다. 캐시를 넣으면 이론상 스텝 수에 가깝게 줄지만, 핀으로 고정한 Kronos 모델 코드를 고쳐야 하고 결과가 비트 단위로 같다는 보장이 없다. 검증하지 않았다 |
| Pod 여러 대로 날짜 나누기 | 가능 | 총비용은 같고 벽시계 시간만 줄어든다. 같은 run_id에는 같은 GPU 종류만 쓴다. 지금 스크립트는 날짜 분할을 지원하지 않는다(`INFER_ARGS="--start --end"`로 run_id를 나누면 가능하나 run_id가 갈린다) |

결론: 공식 코드, float32, batch 256 그대로 돌린다. 두 프로필 합쳐 약 13시간, 약 $10이다.

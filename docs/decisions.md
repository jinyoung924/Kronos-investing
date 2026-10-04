# 결정 기록

사용자가 정한 값과 방침이다. 에이전트는 각 Stage 시작 전에 이 문서를 읽고, 해당 Stage에 적용되는 항목만 반영한다. 설정에 반영할 때는 주석에 결정 번호를 단다(예: `# D-1`). 이 문서가 개요서의 불변 원칙이나 명세와 충돌하면 구현하지 말고 보고서의 "질문"에 남긴다.

## Stage 0 보고서 후속 (2026-10-03)

| 번호 | 대응하는 Stage 0 질문 | 적용 시점 |
| --- | --- | --- |
| D-1 | 질문 3 (price_basis) | 지금 설정, Stage 2 사용 |
| D-2 | 질문 2 (market 키 중복) | Stage 1부터 |
| D-3 | 질문 1 (거래정지 종목 추론 제외) | Stage 2 |
| D-4 | 질문 4 (universe.variant) | Stage 1 |
| D-5 | 부록 C 선택 1 | 부록 C-1 |
| D-6 | Stage 1 선행 결정 (벤치마크) | Stage 1, 확정은 Stage 5 |
| D-7 | 명세서와 달라진 점 2·5 | 지금 |
| D-8 | 질문 5 (requirements) | 지금, 부록 C-1 |
| D-9 | 질문 6 (로그 이름) | — |

### D-1. 추론 입력 가격 기준: raw

- `data.price_basis: raw`로 채운다.
- 근거: scripts/probes/stage0_price_basis.py. 모든 윈도우에서 마지막 종가 = as\_of 원주가, 미래 행 0, rebase 후 분할 흔적 없음(최대 일간 |Δlog close| 0.357 = 하한가 하루).
- C\_signal의 last\_close는 as\_of 원주가 종가다.

### D-2. market 정보의 기준 키: data.markets

- Stage 1 이후 새 코드는 `data.markets`만 참조한다.
- `krx.markets`, `universe.markets`는 지우지 않고 수집 파이프라인과 common/data.load\_prices 전용으로 둔다. 새 코드에서 참조하지 않는다.

### D-3. 400봉 윈도우 안 거래정지 종목: 모든 전략의 종목 집합을 "예측이 있는 종목"으로 맞춘다

배경: build\_batch는 윈도우 안에 NaN(정지일)이 하루라도 있으면 종목을 뺀다. as\_of마다 유니버스의 5~6%가 빠지고, 이대로면 Kronos 전략과 벤치마크 전략의 종목 집합이 달라 비교가 불공정하다. 빠지는 종목에는 분할·감자 전후 정지 종목이 많아, Kronos 쪽에서만 빠지면 결과가 한쪽으로 기울 수 있다.

- build\_batch의 제외 규칙은 바꾸지 않는다. 정지일을 종가로 채우는 방식(보고서 선택지 b)은 채택하지 않는다. A의 유니버스 필터도 바꾸지 않는다.
- **분석 대상 종목 = 그날 백테스트 유니버스 ∩ 그 run\_id 예측 파일에 있는 종목.** C\_signal/run\_signal이 이 교집합으로 시그널 행을 만들고, baseline\_features(mom20, vol20, rev5)도 이 행에만 붙인다. 따라서 D의 모든 전략(EqualWeight, Momentum20, Random 포함)이 같은 종목 집합에서 고른다.
- 가짜 예측도 같은 규칙을 따른다. dummy는 run\_inference(DummyBackend) 경로라 이미 같은 skipped가 적용된다. oracle(make\_fake\_predictions)도 build\_batch와 같은 자격 판정(이력 부족, stale, nan\_in\_window, 0 이하 가격)으로 종목을 거른다. 판정 로직은 build\_batch에서 함수로 분리해 함께 쓴다.
- signals의 meta.json에 날짜별로 유니버스 종목 수, 제외 종목 수, 제외 사유별 수를 남긴다.
- F\_evaluate와 G\_report에 한계로 명시한다: "lookback 안에 거래정지 이력이 있는 종목은 모든 전략에서 제외된다."

### D-4. 백테스트 본 표본 유니버스: base

- `universe.variant: base`를 유지한다.
- liq5, clean은 민감도 분석용이며 run\_id를 달리해 돌리고 trials.csv에 기록한다.

### D-5. 추론 유니버스: liq5 (부록 C-1에서 적용, 지금은 구현하지 않음)

- 실제 Kronos 추론은 liq5 유니버스로 한다. 날짜마다 base ⊆ liq5, clean ⊆ base라서 세 변형을 재추론 없이 쓸 수 있다.
- 백테스트 유니버스(`universe.variant`)와 별개의 키로 둔다. 키 위치는 D-7의 구조를 따른다(예: 추론 프로필 아래 `universe_variant`).
- 백테스트 유니버스로 다시 거르는 일은 D-3의 교집합 규칙이 한다.

### D-6. 벤치마크

- Stage 1은 지수 레벨(KOSPI, KOSDAQ)과 ETF(226490, 229200)를 모두 data/A\_prepared에 노출한다.
- 상대 지표(초과수익, 베타, 알파, TE, IR)의 주 벤치마크는 Stage 5에서 확정한다. 잠정안: 두 거래소를 풀링한 유니버스이므로 같은 종목 집합의 EqualWeight를 주 벤치마크로 쓰고, KOSPI·KOSDAQ 지수 Buy&Hold는 참고선으로 함께 보고한다.

### D-7. 추론 설정 구조: 현재 레포 구조 유지

- `model:`(모델명, 토크나이저, revision, kronos\_repo, device, max\_context, batch\_size, top\_k)와 `infer.profiles.<프로필>`(lookback, pred\_len, step, T, top\_p, sample\_count) 구조를 유지한다.
- spec 부록 C의 `infer:` 예시 키는 이 구조로 대응시키고, 새 섹션을 만들지 않는다.
- seed는 `project.seed`(42) 하나만 쓴다.

### D-8. 의존성

- requirements.txt는 현재 고정 버전을 유지한다. Windows 백테스트 환경에서 설치되지 않는 패키지가 있으면 보고서에 적고 사용자에게 묻는다.
- torch는 requirements.txt에 넣지 않는다. RunPod 추론용 의존성(torch 등)은 부록 C-1에서 requirements-infer.txt로 따로 고정한다.

### D-9. 로그 이름: 유지

- 로거 이름과 logs/data\_prepare.log를 그대로 둔다.

## 아직 정하지 않은 것

아래 키는 null로 두고, 해당 Stage의 보고서에서 사용자에게 요청한다.

| 키 / 항목 | 필요한 Stage |
| --- | --- |
| strategies.momentum20\_topk.k, strategies.random\_topk.k | 3 |
| costs.sell\_tax\_table (출처 확인 후 사용자 기입) | 4 |
| backtest.delist\_policy | 4 |
| 주 벤치마크 확정 (D-6 잠정안), 거래소별 분석 포함 여부 | 5 |
| strategies.topk.k, strategies.vol\_target.k, strategies.conf\_weighted.threshold | 6 |
| backtest.init\_cash, v2.price\_limit\_pct, v2.tick\_table, v2.max\_participation | 7 |

## Stage 1 보고서 후속 (2026-10-03)

### D-10. 오라클 예측 구성 (Stage 2)

- **가격 스케일**: as\_of 원주가 스케일로 만든다. horizon\_step h의 가격 = 원주가(t\_h) × F(t\_h) / F(as\_of), t\_h는 as\_of 이후 h번째 거래일이다. 미래 분할이 가짜 급락으로 나타나지 않게 하기 위해서다. 오라클은 일부러 미래를 쓰는 테스트 장치라 미래 F 사용을 허용하며, 오라클 밖의 코드에서는 금지다.
- **미래 정지일**: open/high/low가 NaN이면 그날 종가(기준가)로 채우고 거래량은 0으로 둔다.
- **미래 상장폐지**: 가격 행이 없는 날은 마지막 유효 종가를 이어 붙이고 거래량은 0으로 둔다. 해당 (as\_of, ticker) 수를 meta.json에 기록한다.
- **노이즈**: 곱셈 노이즈 exp(N(0, ε)). ε는 테스트 상수, seed는 project.seed.
- **종목 자격**: D-3대로 build\_batch와 같은 자격 판정을 쓴다.
- **기대치**: 오라클 exp\_ret는 as\_of 종가 → 5거래일 뒤 종가 기준이고, F\_evaluate 라벨은 다음날 시가 → 그 5거래일 뒤 시가 기준이다. Stage 2 테스트는 종가 기준 실현 수익률과 비교한다. Stage 5의 오라클 IC는 1보다 낮게 나오는 것이 정상이며, 이를 1에 맞추려고 라벨이나 오라클을 고치지 않는다.

### D-11. 시그널 샘플 수 (Stage 2)

- `signal.n_samples`를 **프로필별 매핑**으로 둔다: `{base: 20, paper: 10}` (2026-10-03 수정: spec의 paper 프로필 sample\_count 10과 맞추기 위해. 원래는 단일 값 20이었다).
- C\_signal/aggregate는 (as\_of, ticker)마다 sample\_id 오름차순으로 앞의 n\_samples\[profile\]개만 쓴다. 샘플이 그보다 적으면 오류를 낸다. run\_id의 프로필은 manifest.profile에서 읽는다.
- 예측의 sample\_count가 더 커도(부록 C 선택 2) 시그널은 같은 정의를 유지한다.

### D-12. Stage 2 범위

- spec의 Stage 2 범위를 그대로 구현한다(2026-10-03 수정: spec 우선). 가짜 예측은 spec대로 base·paper 두 프로필 모두 만든다(fake\_{dummy,oracle}\_{base,paper}). "paper 프로필 실행을 추가하지 않는다"는 **실제 Kronos 추론**(RunPod)에만 해당하며 그것은 Stage 6·부록 C에서 한다. spec에 없는 추론 옵션은 추가하지 않는다.
- `universe.top_n_mktcap`은 null로 유지한다.

### D-13. Stage 1 확인 결과

- 시가 0이면서 소량 체결된 2행을 정지로 분류한 것: 승인
- 설명되지 않은 급변 58행을 전부 정리매매로 판단한 것과 정지 해제일 제외 규칙: 승인, 현행 유지
- 벤치마크에 market을 두지 않은 것: 승인. Stage 5에서 ETF에 비용을 붙여야 할 때 추가한다.

## Stage 2 보고서 후속 (2026-10-03)

사용자 지시: "spec의 구현을 우선으로 두고 decisions.md를 맞춘다." 이에 따라 D-11·D-12를 위와 같이 고쳤다. dummy의 정의는 spec 부록 A가 DummyBackend(signal\_strength 0)를 허용하므로 그대로 둔다(D-3 유지). 수행 내용과 새로 결정이 필요한 사항은 각 Stage 보고서의 "명세서와 달라진 점"·"질문" 절에 계속 적는다.

## Stage 3 보고서 후속 (2026-10-03)

### D-14. 벤치마크 전략의 K와 비중 파일 용어

- K(momentum20\_topk, random\_topk)는 전략 파라미터로 둔다(configs `strategies.<name>.k`, 기본 20). 고정값으로 확정하지 않고 실행 시 바꿔 가며 테스트한다: `python -m D_strategy.run_strategy ... --set strategies.random_topk.k=50`. 바꿔 돌린 결과는 run\_id·trials.csv로 구분한다(Stage 5).
- 비중 파일의 날짜 컬럼 이름은 `as_of_date`로 통일한다(예측·시그널과 같은 키). spec Stage 3의 rebalance\_date 표기를 as\_of\_date로 고쳤다. 값은 시그널일(= 리밸런싱 결정일)이고 체결은 다음 거래일이다.
- 보류(NaN) 규약은 Stage 3 구현을 기준으로 한다: 비중 파일에 NaN이 남고, prev\_w에는 직전 목표 비중이 이어진다.

## Stage 4 보고서 후속 (2026-10-03)

### D-15. 비용과 K의 기본값: 논문 investment simulation 값

- 사용자 지시: "cost와 k는 논문 investment simulation에서 사용한 값을 기본값으로 두고 나중에 수정한다."
- `costs.scenario: paper` (매수 0.10%, 매도 0.15%, 세금·슬리피지 없음; 공식 레포 qlib 설정). 한국 세율표(`costs.sell_tax_table`)가 채워지면 kr로 바꾼다. `--costs kr|paper`, `--no-costs`로 언제든 덮어쓴다.
- K = 50 (논문 CSI 300 설정, 공식 레포 n\_symbol\_hold 50): strategies.momentum20\_topk.k, random\_topk.k, vol\_target.k, topk.k = 50, topk.n\_drop = 5, hold\_min\_days 5. CSI 800 대응(k 200, n 10)은 `--set`으로 돌린다.
- 결과 폴더: paper 비용이 기본이므로 기본 결과 폴더는 `@paper_costs`다(Stage 4의 접미사 규칙 유지).
- 미결: `backtest.delist_policy`(Stage 4 질문 2, 제안 last\_close). 정해질 때까지 실행에 `--set backtest.delist_policy=last_close`를 쓴다.

## Stage 5 보고서 후속 (2026-10-03)

### D-16. 벤치마크와 거래소별 분해

- 주 벤치마크는 잠정안대로 `evaluate.benchmark: equal_weight`(같은 종목 집합의 EqualWeight, 같은 엔진·비용 시나리오)를 유지한다. AER·IR 등 상대 지표의 기본 기준이다.
- 논문 재현(TopK, paper 프로필)의 AER·IR 기준으로 **지수 벤치마크를 추가**한다: `evaluate.paper_benchmark: KOSPI`(CSI 300 대응). portfolio\_metrics.csv에 `*_vs_index` 열(aer, ir, beta, alpha, tracking\_error, monthly\_hit)로 함께 나온다.
- 거래소별 분해(`evaluate.by_market: true`): IC는 거래소 **안에서** 횡단면을 잡아 KOSPI·KOSDAQ 따로 계산하고, 전략 성과는 보유 비중 × 일간 수익률을 거래소별로 합산한 기여도(평균 비중, 연율 기여, 서브북 CAGR)로 분해한다(portfolio\_by\_market.csv).

### D-17. Deflated Sharpe의 시도 수 범위 (에이전트 판단)

- 전역 장부 data/F\_metrics/trials.csv에 모든 run\_id의 시도(run\_id, profile, fake 여부, engine, strategy, config\_hash, sharpe)를 쌓는다.
- n\_trials = **같은 프로필의 실제(비가짜) run\_id 시도 수**(엔진·전략·설정 해시가 다른 행 수). 가짜 run\_id(dummy·oracle)는 파이프라인 검증 장치라 실제 결과의 시도 수에 넣지 않고, 가짜 run\_id 자신을 채점할 때는 자기 행만 센다.
- 이유: DSR은 같은 데이터(같은 기간·유니버스·H)에서 "몇 번 시도해 고른 결과인가"를 보정한다. 프로필이 다르면 H와 리밸런싱 주기가 달라 비교 대상이 아니고, 가짜 예측은 선택 과정의 시도가 아니다. K를 `--set`으로 바꾼 실행은 config\_hash가 달라 시도 수에 들어간다.

### D-5 적용 (부록 C-1)

- `infer.profiles.<p>.universe_variant: liq5`. run\_inference·make\_fake\_predictions가 이 변형으로 유니버스를 읽고 manifest에 기록한다. 백테스트 유니버스는 `universe.variant`(base) 그대로이며 C\_signal의 교집합(D-3)이 걸러 준다. 기존 가짜 예측은 liq5로 다시 생성한다.

## 부록 C-1 보고서 후속 (2026-10-04)

### D-18. RunPod 운영 방식: Secure Cloud + 네트워크 볼륨, 코드는 git·데이터는 SSH

- 사용자 지시: Secure Cloud를 쓰고, MMDL-MMMU의 `cloud/` 방식(진입 스크립트 하나, 결과 브랜치, 로컬 헬퍼)을 따라 `RunPod/` 디렉토리에 연결과 결과 회수를 구현한다. 비용보다 안정성과 구현 속도를 우선한다.
- 코드: 로컬 `RunPod/local.sh push-code` → Pod `git clone`. `git archive` 번들(pod\_bundle.py, B\_model\_infer/pod/setup\_pod.sh)은 없앤다.
- 입력 데이터(prices, liq5 유니버스 45 MB): git에 없으므로 `local.sh upload`가 SSH로 보내고, Pod가 커밋된 `RunPod/inputs.sha256.json`과 대조한다(`B_model_infer/pod_inputs.py`).
- 예측 parquet(base 약 250 MB, paper 약 1.3 GB): /workspace 네트워크 볼륨에 쓰고 `local.sh fetch`가 rsync로 회수해 `checksum verify`한다. GitHub에 올리지 않는다(MMDL과 다른 점: 결과가 커서 결과 브랜치에는 메타데이터만 둔다).
- 메타데이터(manifest.json, checksums.json, cloud\_run.json, requirements.lock.txt): Pod가 `results/<run_id>` 브랜치로 push하고 `local.sh merge`가 main에 병합한다.
- Pod는 스스로 종료하지 않는다. `local.sh terminate`가 로컬 verify 통과를 확인한 뒤 API로 종료한다(부록 C 원칙 3). RunPod API 키는 로컬에만 필요하다.
- 자격증명: Secure Cloud이므로 Pod에 fine-grained `GITHUB_TOKEN`(이 레포, Contents read/write)과 `PUBLIC_KEY`를 둔다. 부록 C 원칙 6을 이에 맞게 고쳤다.
- 이미지: 무엇이든 된다. `RunPod/setup_runpod.sh`가 /workspace/venv에 Python 3.12 + requirements-infer.txt(D-8)를 설치한다. 템플릿 값은 RunPod/README.md §5.

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
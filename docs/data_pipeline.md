# 데이터 파이프라인 (KRX Open API → 백테스트용 KOSPI·KOSDAQ 데이터셋)

대상: KOSPI·KOSDAQ 전종목 일봉, 백테스트 구간 2024-07-01 ~ 2025-06-30 (`configs/base.yaml`의 평가 기간; 현재 키 `run`, Stage 0 이후 `period`).
Kronos-base는 2024-06까지의 데이터로 학습됐으므로 그 이후 1년만 out-of-sample 구간으로 쓴다.
코드: [A_data_prepare/](../A_data_prepare/) (Stage 0에서 `data_prepare/`를 이름만 바꿨다. 로직 변경 없음). 실행: `python -m A_data_prepare.run [단계 ...]`.
거래소별로 파일을 나누고 같은 규칙으로 처리해, 풀링 실험과 거래소별 실험을 동일 조건에서 비교할 수 있게 한다 (§9).

이 문서는 **A 단계의 수집·정제·검증**(원본 JSON → `data/raw`, `data/universe`)을 정한다. 그 위에 백테스트가 읽는 `data/A_prepared/`를 만드는 일은
[spec.md](spec.md) Stage 1이, 전체 설계와 불변 원칙은 [outline.md](outline.md)가 정한다. 여기 적힌 파일·경로는 바꾸지 않는다(`MANIFEST.json --verify`가 이 경로 기준).

## 0. 단계와 산출물

```text
probe      서비스별 1회 호출: 승인 여부·스키마·코드 형식 확인     -> data/krx_raw/<snapshot>/probe_findings.json, probe.txt
fetch      엔드포인트 × 평일 1회씩 원본 JSON 캐시 (재개 가능)        -> data/krx_raw/<snapshot>/{api_id}/{YYYYMMDD}.json, PULL_METADATA.json
build      거래소마다 파싱·필터·조정계수·유니버스(변형 포함)·검증    -> data/raw/{kospi,kosdaq}/..., data/universe/..., BUILD_METADATA.json
benchmark  지수 레벨 + ETF                                            -> data/raw/benchmark/prices.parquet
sanity     파일 간 게이트 (§6)                                       -> data/raw/sanity_report.json, diag_*.csv
manifest   sha256 목록                                               -> data/MANIFEST.json
```

```bash
python -m A_data_prepare.run probe                    # 먼저. 미승인 서비스(401)를 수천 번 호출 전에 알아냄
python -m A_data_prepare.run fetch --dry-run          # 필요한 호출 수
python -m A_data_prepare.run                          # 전체 (probe fetch build benchmark sanity manifest)
python -m A_data_prepare.run build benchmark sanity   # 캐시가 있으면 네트워크 없이 파생 단계만 재실행
python -m A_data_prepare.manifest --verify            # 공유받은 data/가 같은 빈티지인지 확인
```

로그는 `logs/data_prepare.log`(타임스탬프 포함)에 남는다. 팀원 프로젝트(`performance-aware-latent-factors/00_data_prep`)의
구조(단계형 오케스트레이터, 빈티지 고정, PULL_METADATA, MANIFEST `--verify`, probe, 하드/소프트 게이트 sanity, end-to-end
외부 기준 검증, 유니버스 포크, 데이터 전달 명세)를 그대로 따랐다.

## 1. 빈티지(snapshot) 고정과 재현성

- `krx.snapshot`(현재 `2026-09-28`)은 원본을 받은 날이다. 원본 JSON은 `data/krx_raw/<snapshot>/` 아래에만 쓰이고, 그 뒤
  build/sanity는 **네트워크를 전혀 쓰지 않는다**. 다시 받으려면 스냅샷 값을 바꿔 새 디렉터리에 받는다(`--force`는 빈티지를
  바꾸므로 경고를 낸다).
- `PULL_METADATA.json`: 엔드포인트별 수집 시각, 기간, 평일 수, 빈 평일 수, 행 수, 호출 수.
- `BUILD_METADATA.json`: 스냅샷, 설정 해시(`config_hash`), 코드 커밋, 빌드 시각, 거래소·변형별 검증 요약.
- `data/MANIFEST.json`: `data/raw`, `data/universe`, `data/krx_raw/<snapshot>` 아래 모든 파일의 바이트 수와 sha256
  (3,664개, 998MB). 데이터 파일은 커밋하지 않고 매니페스트와 메타데이터 JSON만 커밋한다. 데이터를 복사받은 사람은
  `python -m A_data_prepare.manifest --verify`로 바이트 단위 동일성을 확인한다.

## 2. 데이터 전달 명세

| 항목 | 내용 |
|---|---|
| 자료 버전 | snapshot `2026-09-28`, KRX Open API (data-dbg.krx.co.kr). 검증: `manifest --verify` |
| 가격 파일 | `data/raw/{kospi,kosdaq}/prices.parquet`: 키 (`ticker`, `date`), 열 `open high low close volume adj_factor exchange name sect trdval mktcap list_shrs chg fluc_rt halted`. 가격은 **원가격(원)**, `raw × adj_factor` = 수정주가. 거래량은 주, 거래대금·시총은 원 |
| 유니버스 파일 | `data/universe/constituents_{index}[_{variant}].parquet`: (`date`, `ticker`) 거래일별 스냅샷, 2024-06-03 ~ 2025-07-15 (271일). 백테스트는 `date` 이하 최신 스냅샷 사용 |
| 벤치마크 | `data/raw/benchmark/prices.parquet`: `KOSPI`, `KOSDAQ` 지수 레벨(OHLC, `adj_factor=1`), ETF `226490`(KODEX 코스피), `229200`(KODEX 코스닥150). 각 680일 |
| 기간 | 가격 2022-10-04 ~ 2025-07-15 (거래일 680, 평가 시작 이전 428, 평가 종료 이후 11). 백테스트 시그널일 2024-07-01 ~ 2025-06-30 (Kronos 학습 종료 2024-06 이후 1년). 종료 후 11거래일은 라벨(H = 5)용 |
| 캘린더 | KRX 거래일. 주식·지수·ETF 파일 동일(sanity 게이트) |
| 종목 필터 | 보통주(코드 끝 0), 이름 `스팩` 제외, 코스닥 소속부 `SPAC` 제외; 유니버스는 추가로 이력 ≥ 400봉, 20일 평균 거래대금 ≥ 10억, 당일 무거래 제외 (§3) |
| 수익률 정의 | 가격수익률(현금배당 미반영, §5-3). 시그널일 s의 비중은 다음 거래일 f의 시가에 체결, 라벨은 수정 시가 f → f+H (spec Stage 5). 엔진 NAV는 매일 종가로 평가 (spec Stage 4) |
| 상장폐지 | 사후 제거 없음. 정리매매 가격이 데이터에 남는다. 가격이 사라진 뒤의 처리는 `backtest.delist_policy`(last_close 또는 zero)로 정한다 (§4, outline 결정 사항) |
| 부속 진단 | `adjustment_events.csv`, `residual_big_moves.csv`, `validation.json`, `diag_universe_by_month.csv`, `diag_delisted_members.csv`, `coverage_{index}[_{variant}].csv` |

CSV를 pandas로 읽을 때는 `dtype={"ticker": str}`을 줘야 `000480` 같은 코드의 앞자리 0이 보존된다(parquet은 문제없음).

### 2026-09-28 스냅샷 요약
| 항목 | KOSPI | KOSDAQ |
|---|---|---|
| 호출 | `stk_bydd_trd` 726회 | `ksq_bydd_trd` 726회 (지수 2×727, ETF 727 별도) |
| 종목 | 원본 985 → 865 (우선주 등 119, 스팩 1 제외) | 원본 1,937 → 1,770 (우선주 4, 이름 `스팩` 163, 소속부 SPAC 162; 거의 겹침) |
| 기준가 변경 이벤트 | 179건 전부 적용 | 501건 전부 적용 (ratio 0.07~40) |
| 유니버스(base) | 거래일별 339~468 (상장 840~850) | 480~635 (상장 1,632~1,705) |
| 유니버스(liq5 / clean) | 430~575 / 339~468(=base) | 665~860 / 446~595 |
| 구간 중 상장폐지 | 6 (유니버스 구성이었던 것 2) | 26 (7) |
| 잔여 급등락(±35% 초과) | 6행, 전부 정리매매 | 90행, 전부 상장폐지 14일 이내 |
| 이전상장 | 두 거래소에 모두 등장 7종목, 같은 날 중복 0 | |

## 3. KRX Open API 사용법 (알아야 할 것)

- 기본 URL `https://data-dbg.krx.co.kr/svc/apis/{sto|idx|etp}/{api_id}.json`, 헤더 `AUTH_KEY`, 파라미터 `basDd=YYYYMMDD`.
  응답 `{"OutBlock_1": [...]}`, **모든 값이 문자열**. 한 호출 = 하루치 전종목(종목별 기간 조회는 없음).
- **서비스별 이용신청이 따로 필요**하다(인증키와 별개). 미승인이면 401. 현재 키는 아래 5개 모두 승인됨(`probe`로 확인).

  | api_id | 서비스명 | 용도 |
  |---|---|---|
  | `sto/stk_bydd_trd` | 유가증권 일별매매정보 | KOSPI 가격·거래대금·시총·상장주식수, 유니버스 |
  | `sto/ksq_bydd_trd` | 코스닥 일별매매정보 | KOSDAQ 동일 (+ 소속부 `SECT_TP_NM`) |
  | `idx/kospi_dd_trd`, `idx/kosdaq_dd_trd` | 지수 시리즈 일별시세 | 벤치마크 지수 레벨 `KOSPI`, `KOSDAQ` |
  | `etp/etf_bydd_trd` | ETF 일별매매정보 | 거래 가능한 벤치마크 대용 ETF |

- 한도: 키당 **일 10,000회**(429). 전체 수집 약 3,600회. 캐시가 있으면 0회.
- 주말은 요청하지 않는다. 빈 응답 평일 = 휴장일(`[]`로 캐시, 2.8년간 47일). 예외: **ETF 엔드포인트는 휴장일에도 가격이
  빈 문자열인 행을 돌려준다** → 종가 없는 행은 버린다. 일시 장애 의심 시 `--refetch-empty`.
- 응답 필드(주식): `BAS_DD, ISU_CD(6자리), ISU_NM, MKT_NM, SECT_TP_NM, TDD_CLSPRC, CMPPREVDD_PRC(전일대비), FLUC_RT(등락률),
  TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, ACC_TRDVOL, ACC_TRDVAL, MKTCAP, LIST_SHRS`. `SECT_TP_NM`은 KOSDAQ에만 값이 있다
  (우량기업부, 중견기업부, 벤처기업부, 기술성장기업부, SPAC/관리종목/투자주의환기종목/외국기업(소속부없음)); KOSPI는 빈 문자열.
- 키는 `.env`에 키 값만 적거나 `KRX_API_KEY=...`. `.env`는 커밋하지 않는다.

## 4. 생존편향 제거 (point-in-time 유니버스)

핵심: **일별매매정보는 그 날 상장돼 있던 모든 종목을 담고 있다.** 상장폐지 종목도 마지막 거래일까지 나온다.
"현재 상장 종목 리스트"(종목기본정보 API는 현재 상장분만 준다)를 쓰지 않고, **각 날짜의 응답에 등장한 종목 집합**을 그 날의
유니버스로 쓴다.

- `constituents_*.parquet`는 거래일마다 (date, ticker) 스냅샷. 나중에 상장된 종목은 그 전엔 보이지 않고, 나중에 폐지될
  종목은 폐지 전까지 정상 포함된다.
- 상장폐지 후: 가격 행이 사라진다. 정리매매(7거래일, 가격제한 없음)의 −90%대 하락은 가격에 남아 반영된다. 보유 종목의
  가격이 사라진 뒤의 처리는 엔진 설정 `backtest.delist_policy`가 정한다: `last_close`(마지막 가격에 청산, 과대평가 방향) 또는 `zero`(잔존가치 0).
  `diag_delisted_members.csv`: 유니버스 구성이었다가 폐지된 종목의 "마지막 구성일 → 마지막 가격" 수익률
  (KOSPI 4종목 평균 −0.6%, KOSDAQ 8종목 평균 −9.9%). 이 값이 크면 청산 규칙이 결과를 좌우한다는 뜻이다.

유니버스 필터(`configs/base.yaml: universe`). 모두 **as_of 이전 데이터만** 쓰며 `tests/test_data_prepare.py::test_universe_filters_use_only_past`가 검증한다.
이 키들은 Stage 0의 설정 재구성에서도 이름을 유지한다(spec Stage 0 작업 6).

| 필터 | 규칙 | 이유 |
|---|---|---|
| 보통주만 | 6자리 코드 끝자리 `0` | 우선주는 유동성·가격 구조가 다름 |
| 이름 패턴 제외 | 종목명 `스팩` (양 거래소) | SPAC은 현금성 껍데기 |
| 소속부 패턴 제외 | KOSDAQ `SECT_TP_NM`에 `SPAC` | 이름 규칙 보완. 한 날이라도 해당하면 전 기간 제외(표본 출입 방지) |
| 최소 이력 | as_of까지 400봉 이상 | Kronos lookback=400. 벤치마크도 같은 종목군 → 비교 공정성 |
| 유동성 | 20일 평균 거래대금 ≥ 10억원 | 체결 가능성. **가장 강한 필터**(KOSPI 842→451, 5억이면 548) |
| 거래정지 제외 | as_of 당일 무거래면 제외 | 다음날 시가 체결 불가 |

### 유니버스 변형(포크)
팀원 프로젝트의 full/exmicro 포크처럼, 한 번의 build가 `universe.variants`의 모든 변형을 만든다. 어느 변형을 본 표본으로 쓸지는
`universe.variant`(기본 `base`)로 정하고, Stage 1의 `build_universe.py`가 그 변형을 읽어 두 거래소를 합친 `data/A_prepared/universe`를 만든다.
변형을 바꿔 돌린 결과는 run_id를 달리해 저장하고 `trials.csv`에 기록한다(spec Stage 5).

| 변형 | 오버라이드 | 용도 |
|---|---|---|
| `base` | 없음 | 본 표본 |
| `liq5` | 거래대금 ≥ 5억 | 소형주까지 넓힌 민감도 |
| `clean` | 코스닥 소속부 `관리종목`, `투자주의환기` 추가 제외 | 저품질 종목군 효과 분리. KOSPI에는 같은 정보가 없어 **비대칭**(KOSPI는 base와 동일) |

## 5. 가격 데이터 처리

### 5-1. 원가격 유지 + 조정계수 분리
- OHLCV는 KRX 원가격 그대로. 수정주가 = `raw × adj_factor`. `adj_factor`는 종목의 마지막 행에서 1, 과거로 갈수록 이후
  이벤트 비율이 곱해진다. 백테스트는 비율만 쓰고, 추론 입력은 as_of 계수로 rebase하므로 as_of 이후 이벤트는 섞이지 않는다.

### 5-2. 조정계수 산출 (KRX에는 수정주가 API가 없음)
- `CMPPREVDD_PRC`(전일대비) = 종가 − **기준가**. 액면분할/병합·무상증자·유상증자 권리락·감자로 기준가가 재산정되면
  `종가 − 전일대비 ≠ 전일종가`. `ratio = (종가 − 전일대비)/전일종가`가 그 이벤트의 조정 비율 (2:1 분할 → 0.5, 10:1 병합 → 10).
- `adjustment_events.csv`에 이벤트가 남고, ratio가 [0.01, 100] 밖이면 적용하지 않고 표시한다.
- **검증(§6 게이트 2)**: KRX가 함께 주는 `FLUC_RT`(등락률)는 기준가 대비이므로, 우리 조정 수익률과 모든 행에서 일치해야
  한다. 실제 100.0000% 일치(두 거래소). **검증(게이트 5)**: 조정가격·시총으로 재구성한 시가총액가중 수익률과 공식 지수의
  상관 KOSPI 1.0000, KOSDAQ 0.9999 (TE 0.09%, 0.40%). 조정계수·캘린더·시총 자료가 외부 기준과 맞는다는 뜻이다.

### 5-3. 조정계수에 포함되지 않는 것
- **현금배당**: 한국은 현금배당에 기준가 조정을 하지 않으며 KRX Open API에 배당 자료가 없다. 따라서 수익률은
  **가격수익률**이며 배당(코스피 연 1.5~2%)이 빠진다. 전략 전부와 지수(가격지수)에 같은 방향으로 빠지므로 비교는 공정하나
  절대 수익률은 과소평가. DART/KIND 배당을 붙이면 `adj_factor` 한 컬럼만 바꾸면 된다.
- 유상증자 권리락은 기준가 조정에 잡히지만 신주 인수 여부에 따른 실제 수익과는 차이가 있다.

### 5-4. 무거래일 / 거래정지
- 시가·고가·저가 0, 거래량 0인 행(전체 행의 2.1%)은 거래정지 또는 무체결. `open/high/low = NaN, halted = True`, 종가(기준가)만 남긴다.
- `halted` 컬럼이 Stage 1의 halts 표(date, ticker, is_halted)가 된다. 엔진 처리는 spec Stage 4·7을 따른다: v1은 체결일에
  시가가 없는 종목을 거래하지 않고 기존 비중을 유지하며 trades에 표시한다. v2는 halt 제약을 켜면 주문을 거부(`rejected_halt`)하고
  보유 가치는 직전 가격으로 동결한다. 유니버스 필터가 as_of 당일 정지 종목을 제외하므로 시그널 후 정지된 경우만 엔진이 처리한다.
  추론 입력 400봉에 NaN이 있으면 그 종목은 건너뛴다(과거 기준이므로 lookahead 아님, `build_batch` skipped 사유 `nan_in_window`).

### 5-5. 검증 기준(잔여 급등락)
- 조정 후 단순수익률 ±35% 초과 행을 `residual_big_moves.csv`에 남긴다. 상하한가(±30%) 안의 정상 급등락은 안 걸리고
  정리매매·누락 이벤트·데이터 오류만 걸린다. (로그수익률로 잡으면 하한가 −30%가 −0.357로 걸려 오탐. 첫 실행 122건 중 94건.)

## 6. Sanity 게이트 (`python -m A_data_prepare.run sanity`, `data/raw/sanity_report.json`)

하드 게이트는 `AssertionError`로 중단, 소프트는 경고. 2026-09-28 스냅샷 결과:

| # | 게이트 | 종류 | 결과 |
|---|---|---|---|
| 1 | (ticker, date) 유일, `adj_factor > 0`, 거래된 행 `close > 0` | 하드 | PASS |
| 2 | 조정 수익률 == KRX `FLUC_RT` (허용 1e-3, ≥ 99.9% 행) | 하드 | 100.0000% / 100.0000% |
| 3 | 주식·지수·ETF 파일 캘린더 동일 | 하드 | PASS |
| 4 | 유니버스 정합: 구성종목마다 스냅샷일에 거래된 행 존재, 정지 아님, 이력 ≥ 400, 스냅샷일이 거래일 | 하드 | PASS |
| 5 | 구성종목 시총가중 수익률 vs 공식 지수 상관 ≥ 0.90 (하드), ≥ 0.97 (소프트) | 하드+소프트 | KOSPI 1.0000, KOSDAQ 0.9999 |
| 6 | ETF vs 지수 일수익률 상관 ≥ 0.9 | 소프트 | 226490/KOSPI 0.983, 229200/KOSDAQ 0.945 |
| 7 | 진단: 월별 상장·구성·정지·폐지 수(`diag_universe_by_month.csv`), 폐지 구성종목 손실(`diag_delisted_members.csv`), 잔여 급등락 | 기록만 | 위 §2·§4 |

build 단계의 `validation.json`은 파일 단위 하드 검사(중복, 계수 부호, 평가 시작 이전 거래일 ≥ 400, 평가 종료 이후 ≥ H+1)를 따로 한다.

## 7. Lookahead 관련 정리 (데이터 계층)
- 유니버스·유동성·이력·정지 필터는 as_of 이전 행만 사용 (단위 테스트).
- 조정계수는 비율만 사용하고 추론 윈도우는 as_of 계수로 rebase (`B_model_infer/build_batch.py`, `tests/test_no_lookahead.py`).
  여기 저장된 `adj_factor`는 마지막 행이 1인 후진 누적이다. Stage 1은 이를 첫날이 1인 전진 누적 F로 바꿔 `data/A_prepared/adj_factor`에 둔다. 두 날짜 사이 비율은 같다(spec Stage 1).
- 시그널은 종가, 체결은 다음 거래일 시가. 엔진이 `signal_date < fill_date`를 assert (`common/lookahead.py`).
- 라벨(실현수익률)은 전략에 전달되지 않으며 `F_evaluate/labels.py`에서만 계산한다 (outline 불변 원칙 3).

## 8. 알려진 한계 (해석 시 유의)
1. 배당 미반영(§5-3). 2. 상장폐지 잔존가치 0 처리 없음(§4). 3. 신규상장은 이력 필터로 장기간 제외(§4).
4. 거래정지 중 평가가 직전가 기준(§5-4). 5. 관리종목·투자주의환기 정보는 KOSDAQ에만 있어 base에서는 필터하지 않는다(`clean` 변형 참고).
6. 코스피·코스닥 지수는 가격지수라 배당 차이가 전략과 같은 방향으로 빠져 있다. 7. 증권거래세 연도별 변화(2024 0.18% → 2025 0.15%)는 데이터가 아니라
   비용 모델(`costs.sell_tax_table`, 시장·시행일별)에서 반영한다. 세율 값은 사용자가 출처를 확인해 넣는다(spec 공통 규칙 10).

## 9. KOSPI vs KOSDAQ 비교 실험을 위한 공정성 설계

| 요소 | 설계 | 이유 |
|---|---|---|
| 파일 구조 | 거래소별 파일 + `exchange` 컬럼. Stage 1이 합쳐 `market` 컬럼으로 `data/A_prepared/prices`에 둔다 | 풀링·개별 실험을 한 데이터셋으로 |
| 필터 규칙 | `universe` 설정 하나를 두 거래소에 동일 적용 | 규칙 차이가 성과 차이로 둔갑하지 않게 |
| 유동성 절대기준 | 같은 10억(같은 체결 가능성). 통과 비율을 맞추려면 `min_avg_trdval: {kospi: 1e9, kosdaq: 5e8}` | 두 정의 중 선택을 설정으로 명시 |
| 추론 | 한 `run_id`가 두 거래소를 모두 포함. 같은 모델·날짜·샘플 수 | 예측 조건 동일 |
| 거래소별 분석 | 데이터는 `market` 컬럼으로 언제든 나눌 수 있다. 새 설계(outline·spec)는 두 거래소를 풀링한 run_id 하나를 기본으로 하며, 거래소별 실행·지표 분해를 넣을지는 결정 사항이다 | 같은 예측 파일로 거래소별 성과를 볼 수 있게 데이터 쪽은 준비해 둔다 |
| 벤치마크 | 지수 레벨(KOSPI, KOSDAQ)과 ETF(226490, 229200)를 모두 제공한다. 무엇을 벤치마크로 쓸지는 spec Stage 1·5의 선행 결정 | 유니버스 편향 없는 상대 성과 |
| 예측 품질 | 풀링 IC는 거래소 간 수준 차이가 섞이므로, 거래소별 IC를 보려면 거래소 **안에서** 횡단면을 잡아야 한다 (F_evaluate에 분해를 넣을 때 참고) | 비교의 공정성 |
| 비용 | 두 거래소에 같은 비용 모델(수수료 0.015%, 슬리피지 5bp, 거래세는 `costs.sell_tax_table`). 거래세는 2024년 양쪽 0.18%, 2025년 양쪽 0.15%로 동일(사용자 확인) | 비용 차이 없음 |
| 캘린더 | 동일(KRX, sanity 게이트 3) | 리밸런싱일 일치 |
| 이전상장 | 두 파일에 서로 다른 날짜로 존재, 같은 날 중복 0 | 중복 계상 방지 |

해석 시 주의 1: 코스닥 20종목 포트폴리오는 개별 종목 분산이 커서 우연 변동이 크다. 노이즈 예측(더미) TopK가 시드에 따라
−24%~−46%, 랜덤 전략이 −17%~−38%로 순서가 뒤집힌다. 부트스트랩 CI와 여러 시드를 함께 보고, K를 키우거나 IC·분위
스프레드 같은 횡단면 지표를 우선할 것.

해석 시 주의 2: 코스닥 base 유니버스에는 관리종목·투자주의환기 종목이 남아 있다. 코스닥 성과가 나쁘면 모델 문제인지
저품질 종목군 문제인지 `clean` 변형으로 분리해 볼 것.

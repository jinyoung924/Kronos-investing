# Kronos-investing 자작 백테스트 엔진 구현 명세서

Oct 1, 2026 · @진영

## 사용법

코딩 에이전트에게 한 번에 한 Stage만 맡기고, 보고서를 확인한 뒤 다음 Stage를 요청한다. 이 명세서는 각 Stage의 "어떻게"를 정하고, 개요서는 "왜"와 불변 원칙을 정한다. 두 문서가 충돌하면 개요서의 불변 원칙이 이긴다.

**준비**: 개요서와 이 명세서를 Markdown으로 내보내 레포의 docs/outline.md, docs/spec.md에 둔다. 보고서는 docs/stage\_reports/에 쌓인다.

**한 Stage의 흐름**

1. 해당 Stage의 "선행 결정"을 먼저 정하고 configs/base.yaml에 채운다.
2. 아래 요청 문구에서 N만 바꿔 에이전트에게 보낸다.
3. 에이전트가 docs/stage\_reports/stageN.md를 남기고 멈추면, 보고서의 "사용자 확인 요청"을 직접 확인한다.
4. 문제가 없으면 커밋을 확인하고 다음 Stage를 요청한다. 문제가 있으면 같은 Stage 안에서 수정을 요청한다.

**요청 문구 템플릿**

```text
docs/outline.md, docs/spec.md의 "Stage N" 장, docs/stage_reports/ 아래 이전 보고서를 읽어줘.
그다음 Stage N만 구현해. 명세서의 공통 규칙을 지키고, 수정·호출할 기존 코드를 먼저 읽어.
명세와 실제 레포가 다르면 레포 기준으로 맞추고 보고서에 적어.
[확인 필요] 항목은 추측하지 말고 scripts/probes/ 아래 probe로 확인해.
끝나면 pytest 전체와 이 Stage의 실행 명령을 돌리고,
docs/stage_reports/stageN.md를 형식대로 작성한 뒤 "stageN: <요약>"으로 커밋하고 멈춰.
다음 Stage는 시작하지 마.
```

**단계 보고서 형식**

```markdown
# Stage N 보고서
## 구현한 것 (파일 목록과 한 줄 설명)
## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)
## 완료 기준 체크 (명세서 항목별 통과/실패)
## [확인 필요] 항목의 probe 결과
## 명세서와 달라진 점과 이유
## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)
## 질문과 미결정 사항
```

## 공통 규칙

아래 규칙은 모든 Stage에 적용되며, Stage별 명세보다 우선한다.

**범위**

1. 지정된 Stage의 파일만 만들거나 고친다. 다른 폴더를 고쳐야 하면 이유를 보고서에 적는다.
2. 명세의 함수명·경로·컬럼명은 설계 의도다. 실제 레포와 다르면 레포에 맞추고 보고서의 "명세서와 달라진 점"에 적는다.
3. 명세가 틀렸거나 개요서 원칙과 충돌하면 임의로 해결하지 않는다. 선택지를 정리해 보고서의 "질문"에 남긴다.
4. 다음 Stage를 시작하지 않는다.

**구조**

5. 단계 폴더(A\_ \~ G\_)는 패키지(**init**.py 포함)이며, 실행은 `python -m E_backtest.run_backtest`처럼 한다.
6. 단계 폴더끼리 import하지 않는다. 모든 단계는 `common/`만 import할 수 있다. 단계 사이는 data/ 아래 파일로만 연결한다. `scripts/run_pipeline.py`는 단계 CLI를 subprocess로 순서대로 호출만 하며 단계 패키지를 import하지 않는다.
7. 각 단계의 진입점은 `run_*.py` 하나다. 파일 읽기·쓰기는 진입점에서만 하고, 나머지는 DataFrame을 받아 DataFrame을 돌려주는 순수 함수로 작성한다.
8. 경로는 `common/paths.py`의 함수로만 만든다. 코드에 경로 문자열을 직접 쓰지 않는다. Windows 호환을 위해 pathlib를 쓴다.

**설정과 사실값**

9. 모든 파라미터는 configs/base.yaml에서 읽는다. 비용, 세율, K, 기간 같은 숫자를 코드에 쓰지 않는다. 테스트용 숫자는 테스트 파일 안에 둘 수 있다.
10. 세율, 호가단위, 가격제한폭 같은 외부 사실값은 에이전트가 채우지 않는다. 설정에 null로 자리만 두고 보고서에서 사용자에게 요청한다. 값이 null인데 필요하면 명확한 오류로 멈춘다.
11. &#91;확인 필요\] 표시가 있는 데이터·라이브러리 동작은 scripts/probes/stageN\_\*.py로 실제로 확인하고 그 결과대로 구현한다.

**품질**

12. 기능과 테스트를 함께 작성한다. 테스트가 없는 기능은 완료로 보지 않는다.
13. 모든 실행 결과 폴더에는 meta.json(run\_id, 단계, 전략, 엔진, 설정 해시, git 커밋 해시, 입력 파일 해시, 실행 시각)을 남긴다.
14. 난수는 모두 설정의 seed로 고정한다.
15. 새 의존성은 requirements.txt에 버전을 고정해 추가하고 보고서에 적는다.
16. 멀티프로세싱을 쓰는 스크립트는 `if __name__ == "__main__":` 아래에서 실행한다.

## 데이터 파이프라인과의 관계

수집·정제·검증은 [docs/data\_pipeline.md](data_pipeline.md)가 정하고, 코드는 `data_prepare/`(Stage 0 이후 `A_data_prepare/`)에 있다. 이 명세서는 그 산출물을 입력으로 받는 쪽만 정한다. 두 문서가 다루는 범위가 겹치면 데이터 파이프라인 문서가 데이터 쪽 기준이다.

**있는 것** (스냅샷 2026-09-28)

| 항목 | 위치 | 내용 |
| --- | --- | --- |
| 원본 | data/krx\_raw/2026-09-28/{api\_id}/{YYYYMMDD}.json | 엔드포인트 × 평일 1회 캐시, PULL\_METADATA.json. 빈티지로 고정, build 이후 네트워크 사용 없음 |
| 가격 | data/raw/{kospi,kosdaq}/prices.parquet | 2022-10-04 \~ 2025-07-15 (거래일 680). 원주가 + adj\_factor(후진 누적) + halted + 거래대금·시총·상장주식수. 보통주만, 스팩 제외 |
| 조정 이벤트 | data/raw/{index}/adjustment\_events.csv | 기준가 변경 역산 (KOSPI 179건, KOSDAQ 501건). FLUC\_RT와 100% 일치 |
| 유니버스 | data/universe/constituents\_{index}[\_{variant}].parquet | 거래일별 point-in-time 스냅샷 2024-06-03 \~ 2025-07-15. 변형 base / liq5 / clean. 상장폐지 종목은 폐지일까지 남음 |
| 벤치마크 | data/raw/benchmark/prices.parquet | 지수 KOSPI, KOSDAQ (adj\_factor 1) + ETF 226490, 229200 |
| 검증 | data/raw/sanity\_report.json, validation.json, diag\_\*.csv | 하드·소프트 게이트 7개 (중복, FLUC\_RT 일치, 캘린더 동일, 유니버스 정합, 지수 재구성 상관 ≥ 0.9, ETF 상관, 진단) |
| 재현성 | data/MANIFEST.json, BUILD\_METADATA.json | 3,664개 파일 sha256. `python -m data_prepare.manifest --verify` |

**이 명세서에 영향을 주는 사실**

1. 조정계수는 기준가 역산으로 이미 확정·검증됐다. Stage 1은 전진 누적으로의 변환과 events 저장만 한다.
2. 거래정지일은 응답에 남고 시가·고가·저가 0, 거래량 0, 종가만 유효하다. `halted` 컬럼이 이미 있다. Stage 7의 halt 제약은 이 컬럼을 쓴다.
3. 유니버스 필터(보통주, 스팩 제외, 이력 ≥ 400봉, 20일 평균 거래대금 ≥ 10억, 당일 무거래 제외)는 as\_of 이전 데이터만 쓰며 단위 테스트가 있다. D\_strategy는 유니버스 밖 종목을 받지 않는다.
4. 수익률은 가격수익률이다(현금배당 미반영, 지수도 가격지수). 전략과 벤치마크에 같은 방향으로 빠지므로 비교는 공정하나 절대 수준은 과소평가다. F\_evaluate 보고서에 명시한다.
5. 상장폐지 종목은 정리매매 가격이 데이터에 남고 가격이 사라진 뒤의 잔존가치 처리는 없다. backtest.delist\_policy가 이를 정한다.
6. 평가 기간은 2024-07-01 \~ 2025-06-30으로 확정이다. Kronos-base의 학습 데이터가 2024-06에 끝나므로 그 이후 1년만 out-of-sample이다. 가격 데이터는 2025-07-15까지 있어 마지막 시그널일의 라벨(H = 5)까지 덮는다. 기간을 늘리려면 모델 학습 구간과 겹치지 않는지 먼저 따져야 하고, 데이터는 `krx.snapshot`을 바꿔 새 빈티지를 받는다.
7. 증권거래세는 2024년 0.18%, 2025년 0.15%(두 거래소 동일)로 문서에 적혀 있으나 사용자가 출처를 확인해 costs.sell\_tax\_table에 넣는다.

**규칙**

- 수집 코드와 데이터 경로는 바꾸지 않는다. Stage 0에서 경로 참조를 common/paths.py로 모으는 것까지만 한다.
- 데이터를 다시 받을 때는 `krx.snapshot`을 새 날짜로 바꿔 새 디렉터리에 받는다. 기존 빈티지는 덮어쓰지 않는다.
- 데이터 파일은 커밋하지 않는다. MANIFEST.json과 메타데이터 JSON만 커밋하고, 다른 머신(Windows 백테스트 환경)에서는 `manifest --verify`로 동일성을 확인한다.

## 단계 로드맵

구현은 Stage 0\~8의 9번으로 나눈다. 폴더 문자(A\~G)는 데이터가 흐르는 순서이고, Stage 번호는 만드는 순서다. 가짜 예측으로 파이프라인을 먼저 완성하고(Stage 1\~5), 실제 Kronos 예측은 Stage 6에서 처음 붙인다.

| Stage | 다루는 폴더 | 만드는 것 | 게이트 (통과해야 다음으로) |
| --- | --- | --- | --- |
| 0 | 전체, common | 폴더 이름 변경, 공용 모듈, 설정 뼈대, 테스트 환경 | pytest 통과, 기존 수집 스크립트가 새 경로에서 동작 |
| 1 | A\_data\_prepare | 가격, 조정계수, 거래일·거래정지, 유니버스 | 분할 종목 수익률 연속, 유니버스가 스냅샷과 일치 |
| 2 | B\_model\_infer, C\_signal | 더미·오라클 예측, 시그널 집계 | 오라클 시그널이 실현 수익률과 일치, 미래 변경 불변 |
| 3 | D\_strategy | 전략 인터페이스·레지스트리·파일 분리, `--strategy` 인자, 보류 규약, 벤치마크 전략 3개 | 비중 제약 테스트 통과 |
| 4 | E\_backtest | 엔진 v1, 비용 모델 | 장난감 예제·오라클·랜덤 테스트 통과 |
| 5 | F\_evaluate | 라벨, 예측 지표, 성과 지표, 유의성 | quantstats와 지표 일치 |
| 6 | B, D, 전체 실행 | Kronos 전략 3개(TopK = 논문 Top-k/Drop-n 재현), 실제 예측 2종(base·paper 프로필) 검증·연결 | 두 run\_id로 C→D→E(v1)→F가 끝까지 돈다 |
| 7 | E\_backtest | 엔진 v2와 제약 6종 | 제약을 모두 끈 v2가 v1과 일치 |
| 8 | G\_report, scripts | run\_id 간 전략 비교 리포트, shortfall 분해, run\_pipeline | 명령 하나로 리포트 두 개 생성 |

각 Stage 장은 목표, 선행 조건과 선행 결정, 작업, 산출물, 테스트, 완료 기준, 사용자 확인 순서로 쓴다.

## 공통 설정

모든 파라미터는 configs/base.yaml 하나에 둔다. Stage 0에서 아래 뼈대를 만들고, 각 Stage가 자기 키를 채워 나간다. `[사용자]` 표시는 사용자가 직접 정하거나 확인해 넣는 값이고, 에이전트는 null로 남긴다.

```yaml
project:
  run_ids:                 # 프로필별 실제 예측 run_id. 가짜 예측은 fake_dummy_{profile}, fake_oracle_{profile}
    base: kronos_base_v1
    paper: kronos_paper_v1
  seed: 42
period:
  start: 2024-07-01        # 확정: Kronos-base 학습 데이터 종료(2024-06) 이후 1년 = out-of-sample
  end: 2025-06-30          # 가격 데이터는 2025-07-15까지 있어 라벨(H=5)을 덮는다
data:
  markets: [KOSPI, KOSDAQ]
  price_basis: null        # [사용자] raw | adjusted — Kronos 추론 입력과 같은 기준
  index_tickers: {}        # [사용자] 벤치마크 지수 (데이터가 있을 때)
universe:
  name: null               # [사용자] 유니버스 정의 (data_pipeline.md의 variant: base | liq5 | clean)
  top_n_mktcap: null       # [사용자] 정수면 as_of 시총 상위 N개만 남긴다 (논문 CSI 300 대응안: KOSPI 200). null이면 전체
infer:                     # 추론 프로필. run_id 하나는 프로필 하나를 따르고 manifest.profile에 기록한다
  default_profile: base
  profiles:
    base:                  # 이 프로젝트의 기본 설정 (주간 리밸런싱)
      lookback: 400
      pred_len: 5          # H
      step: 5              # as_of 간격(거래일). 5 = 주간
      temperature: 1.0
      top_p: 0.9
      sample_count: 20
    paper:                 # 원 논문 투자 시뮬레이션 설정 (§4.2.3, 부록 D.3.3, 표 6; 부록 C)
      lookback: 90
      pred_len: 10
      step: 1              # 매 거래일 순위 갱신
      temperature: 0.6
      top_p: 0.9
      sample_count: 10     # 논문 표 6: N = 10. 공식 레포 finetune/config.py는 5
signal:
  mom_window: 20
  vol_window: 20
  rev_window: 5
strategies:                # 전략 하나 = 파일 하나. 키 = 파일명 = 레지스트리 이름. profile은 쓰는 예측의 프로필, schedule은 weekly(H 간격) | daily
  equal_weight:    {profile: base, schedule: weekly}
  momentum20_topk: {profile: base, schedule: weekly, k: null}                 # [사용자] K
  random_topk:     {profile: base, schedule: weekly, k: null}                 # [사용자] K
  topk:            {profile: paper, schedule: daily, signal_col: exp_ret_mean,
                    k: null, n_drop: null, hold_min_days: 5}                   # [사용자] 논문 CSI 300: k 50, n 5 / CSI 800: k 200, n 10 (결정 사항)
  conf_weighted:   {profile: base, schedule: weekly, signal_col: exp_ret, threshold: null}   # [사용자] 임계치
  vol_target:      {profile: base, schedule: weekly, signal_col: exp_ret, k: null}           # [사용자] K
costs:
  scenario: kr             # kr (아래 한국 비용) | paper (원 논문 비용, 민감도 비교용)
  commission_buy: 0.00015
  commission_sell: 0.00015
  slippage_bps: 5
  sell_tax_table: []       # [사용자] [{market, start, rate}, ...]
  paper: {buy: 0.0010, sell: 0.0015}   # 공식 레포 qlib 설정 open_cost 0.1%, close_cost 0.15% (논문 본문은 "거래당 0.15%"). 세금·슬리피지 없음
backtest:
  init_cash: null          # [사용자] 초기 운용 자금(원)
  delist_policy: null      # [사용자] last_close | zero
  v2:
    constraints:           # shortfall 분해에서 하나씩 켠다
      costs: true
      integer_shares: true
      cash: true
      price_limit: true
      halt: true
      liquidity: true
    price_limit_pct: null  # [사용자] 가격제한폭
    tick_table: []         # [사용자] [{min_price, tick}, ...]
    max_participation: null  # [사용자] 주문금액 / 당일 거래대금 상한
evaluate:
  quantiles: 5
  bootstrap: {n: 2000, block: 20}
  regimes: {split: half_year}
report:
  compare_run_ids: [kronos_base_v1, kronos_paper_v1]   # compare.md가 한 표에 모을 run_id들
  shortfall_strategy: topk
  shortfall_order: [costs, integer_shares, cash, price_limit, halt, liquidity]
```

설정 해시는 `common/config.py`가 키를 정렬한 JSON으로 만든 뒤 계산한다. 같은 내용이면 키 순서가 달라도 같은 해시가 나와야 한다.

## Stage 0. 기반 정리

**목표**: 기존 레포를 새 구조(A\_ \~ G\_)로 옮기고, 모든 Stage가 쓸 공용 모듈·설정·테스트 환경을 만든다. 새 기능은 만들지 않는다.

**선행 결정**: 없음

**작업**

1. 레포 현황을 먼저 조사해 보고서에 적는다: 기존 폴더, data/ 아래 파일 종류와 크기, common/의 공개 함수, 기존 테스트.
2. 코드 폴더를 옮긴다(git mv). data\_prepare/ → A\_data\_prepare/, infer/ → B\_model\_infer/. 기존 backtest/, scripts/는 사전 정리에서 삭제됐다. 남아 있는 공용 코드는 부록 A를 따라 그대로 쓰고, 삭제된 구현을 참고하려면 부록 B의 커밋에서 본다.
3. C\_signal/, D\_strategy/, E\_backtest/, F\_evaluate/, G\_report/를 빈 패키지로 만든다.
4. 수집 파이프라인의 데이터 폴더는 옮기지 않는다. data/krx\_raw, data/raw, data/universe, data/MANIFEST.json은 docs/data\_pipeline.md의 경로를 그대로 쓴다(`manifest --verify`가 이 경로 기준이다). 예측은 data/B\_predictions/{run\_id}/에 쓰도록 설정(data.predictions\_dir)과 common/paths.py를 바꾸고, 비어 있는 data/predictions/는 지운다. 경로 대응표를 보고서에 남긴다.
5. common/에 아래 모듈을 만들거나 보강한다. 기존 공개 함수는 이름과 동작을 유지한다.
6. configs/base.yaml을 "공통 설정" 뼈대대로 만든다. 기존 설정 값이 있으면 옮겨 오고 출처를 주석으로 단다. 수집 파이프라인(data\_prepare)이 읽는 키(krx.\*, universe.\* 필터와 variants, data.raw\_dir, data.universe\_dir, data.logs\_dir, data.price\_file)는 이름과 위치를 바꾸지 않는다. 그래야 `python -m A_data_prepare.run build`가 같은 파일을 만든다.
7. A\_data\_prepare/와 B\_model\_infer/의 경로 참조를 common/paths.py로 바꾼다. 로직은 바꾸지 않는다.
8. requirements.txt(버전 고정, 기존 pyproject.toml의 dependencies와 일치), pytest.ini(testpaths = tests), scripts/probes/ 폴더를 만든다.

| 모듈 | 공개 함수 (설계 의도) |
| --- | --- |
| common/paths.py | raw\_dir(), prepared\_path(name), predictions\_dir(run\_id), signals\_path(run\_id), weights\_path(run\_id, strategy), backtest\_dir(run\_id, engine, strategy), metrics\_dir(run\_id), report\_dir(run\_id) |
| common/config.py | load\_config(path) → dict, config\_hash(cfg) → str, require(cfg, key) (null이면 오류) |
| common/schema.py | 예측·가격·시그널·비중·NAV 컬럼 정의와 validate\_\*(df) |
| common/lookahead.py | assert\_no\_future\_rows(df, as\_of), assert\_fill\_after\_signal(trades) |
| common/meta.py | write\_meta(out\_dir, cfg, inputs, extra), git\_commit\_hash(), file\_hash(path) |

**산출물**: 새 폴더 구조, common/ 모듈, configs/base.yaml, requirements.txt, pytest.ini

**테스트**: tests/test\_common.py — config\_hash가 키 순서에 무관함, require가 null에서 오류를 냄, 각 validate\_\*가 잘못된 컬럼·타입을 거부함, paths 함수가 run\_id별로 겹치지 않는 경로를 만듦

**완료 기준**

- [ ] pytest 전체 통과 (기존 tests/test\_data\_prepare.py, tests/test\_no\_lookahead.py 포함)
- [ ] 기존 수집 스크립트를 새 경로에서 실행해도 같은 파일이 나온다 (`python -m A_data_prepare.run build benchmark sanity` 후 `manifest --verify`)
- [ ] `python -m B_model_infer.run_inference --backend dummy` 가 data/B\_predictions/ 아래에 validate\_predictions를 통과하는 파일과 manifest.json을 쓴다 (하루치로 확인)

**사용자 확인**: 경로 대응표, 레포 현황 조사 결과, 추론 입력의 가격 기준(부록 A의 build\_batch 항목)이 실제 코드와 일치하는지

## Stage 1. A\_data\_prepare — 기준 데이터

**목표**: KRX 원천 데이터에서 이후 모든 단계가 쓰는 가격, 조정계수, 거래일·거래정지, 유니버스를 만든다.

**선행 결정**: 유니버스 변형(base / liq5 / clean), 벤치마크로 지수 레벨과 ETF 중 무엇을 쓸지. 조정계수 생성 방법은 기준가 역산으로 이미 확정됐다(아래).

**기존 산출물과의 연결**: 수집·정제·검증은 끝나 있다(docs/data\_pipeline.md). Stage 1은 원본 JSON을 다시 파싱하지 않고 아래 입력으로 data/A\_prepared/를 만든다. 필터·조정계수 로직을 다시 만들지 않는다.

| Stage 1 산출물 | 입력 (기존 파일) | 할 일 |
| --- | --- | --- |
| prices | data/raw/{kospi,kosdaq}/prices.parquet (date, ticker, open, high, low, close, volume, adj\_factor, exchange, name, sect, trdval, mktcap, list\_shrs, chg, fluc\_rt, halted) | 두 거래소를 합치고 컬럼명을 명세로 통일 (exchange → market, trdval → value, list\_shrs → listed\_shares). 정지일의 open/high/low는 NaN |
| calendar, halts | 같은 파일의 date 집합과 halted 컬럼 | sanity 게이트 3이 주식·지수·ETF 캘린더 동일을 보장한다 |
| adj\_factor, events | 같은 파일의 adj\_factor(후진 누적, 마지막 행 1)와 data/raw/{index}/adjustment\_events.csv (date, ticker, name, prev\_close, base\_price, ratio, applied) | 전진 누적 F로 변환 (아래 정의). 두 날짜 사이 비율이 기존 adj\_factor와 같음을 테스트한다 |
| universe | data/universe/constituents\_{index}[\_{variant}].parquet | universe.variant로 고르고 두 거래소를 합친다(market 컬럼). 스냅샷은 거래일마다 있다 |
| benchmark | data/raw/benchmark/prices.parquet (지수 KOSPI, KOSDAQ; ETF 226490, 229200; adj\_factor = 1, market 컬럼) | 그대로 복사하거나 경로만 노출 |

**작업**

1. **probe** (scripts/probes/stage1\_halts.py) \[확인 필요\]: docs/data\_pipeline.md §5-4에 따르면 정지일은 응답에 남되 시가·고가·저가 0, 거래량 0으로 나오고 종가(기준가)만 유효하며, 현재 prices.parquet의 `halted` 컬럼이 그 규칙으로 만들어져 있다. 알려진 거래정지 종목·날짜 몇 개를 data/krx\_raw/<snapshot>/ 캐시에서 직접 열어 이 규칙이 맞는지, 상장폐지 외에 응답에서 아예 빠지는 경우가 있는지 확인한다. 네트워크 호출은 하지 않는다.
2. **build\_prices.py**: 원본을 하나의 긴 표로 정제한다. 컬럼은 date, ticker, market, open, high, low, close, volume, value(거래대금)이며, 원본에 상장주식수가 있으면 listed\_shares도 둔다. 가격은 원주가 그대로다.
3. **build\_calendar.py**: 거래일 표(calendar)와 종목별 거래정지 표(halts: date, ticker, is\_halted)를 만든다. 정지 판정 규칙은 probe 결과를 따른다. `common/calendar.py`에 next\_trading\_day(d), shift\_trading\_days(d, n), rebalance\_dates(start, end, H)를 둔다.
4. **build\_adj\_factor.py**: 조정계수를 만든다 (아래 정의).
5. **build\_universe.py**: 기존 구성종목 스냅샷에서 universe(date, ticker)를 만들고, `common/universe.py`에 get\_universe(date) → 그 날짜 이하 최신 스냅샷을 둔다.
6. **run\_prepare.py**: 2\~5를 순서대로 실행하고 data/A\_prepared/에 저장한다.

**조정계수 정의**: 미래 정보를 쓰지 않도록 전진 누적 계수를 쓴다. 각 종목의 첫 날 F = 1.0이고, 기업행위가 있는 날(ex-date)에만 F가 바뀐다. 수정가격은 raw × F다. 이 방식은 그날까지의 사건만 반영하므로 lookahead가 없다. 함께 events(ticker, ex\_date, ratio, source) 표를 저장해 근거를 남긴다.

```latex
F_t = F_{t-1} \times r_t, \quad r_t = \frac{\text{조정 전 기준가}_t}{\text{조정 후 기준가}_t} \; (\text{사건이 없으면 } r_t = 1)
```

기존 파이프라인의 ratio는 반대 방향이다: `ratio = (종가 − 전일대비) / 전일종가 = 조정 후 기준가 / 조정 전 기준가` (2:1 분할 → 0.5)이고, 기존 `adj_factor[t] = Π_{ex_date > t} ratio` (마지막 행 1). 따라서 `r_t = 1 / ratio`이고 `F_t = Π_{ex_date ≤ t} (1 / ratio)`다. 어느 쪽이든 `raw × factor`는 연속 수정가격이고 두 날짜 사이의 비율은 같다. 적용 범위 [0.01, 100] 밖의 ratio는 기존처럼 적용하지 않고 events에 표시한다.

**산출물**: data/A\_prepared/ 아래 prices, calendar, halts, adj\_factor, events, universe (parquet), meta.json

**테스트** (tests/test\_stage1\_data.py)

- 분할 샘플 종목에서 수정 종가 일간 수익률이 ex-date에 가격제한폭 범위 안에 있다
- 전 종목에서 수정 수익률이 가격제한폭을 넘는 날을 모아 "설명되지 않은 급변" 목록으로 출력한다 (신규 상장일, 정지 해제일 제외)
- 조정계수 계산에 미래 날짜 데이터를 바꿔도 과거 F가 변하지 않는다
- get\_universe(d)가 d 이하 최신 스냅샷과 같고, 상장폐지 종목이 폐지일까지 남아 있다
- (date, ticker) 중복 없음, 가격 음수 없음

**완료 기준**

- [ ] 테스트 통과
- [ ] "설명되지 않은 급변" 목록이 보고서에 있고, 각 건에 대한 판단(누락 이벤트 / 정상)이 적혀 있다

**사용자 확인**: 유명한 분할 종목 2\~3개의 원주가·수정주가 그래프, 날짜별 유니버스 종목 수 그래프, 거래정지 probe 결과

## Stage 2. B 가짜 예측 + C\_signal — 시그널

**목표**: Kronos 없이 파이프라인을 시험할 가짜 예측 두 종류를 만들고, 예측 샘플을 시그널로 요약하는 C 단계를 완성한다.

**선행 결정**: price\_basis (Kronos 추론 입력과 같은 가격 기준)

**작업**

1. **B\_model\_infer/run\_inference.py**에 `--profile base|paper`를 추가한다. lookback, pred\_len, step, T, top\_p, sample\_count를 infer.profiles.\<profile>에서 읽고 manifest.json에 `profile`과 그 값들을 기록한다. as\_of 날짜는 rebalance\_dates(start, end, step)이다(step 1이면 매 거래일). 로직은 바꾸지 않는다.
2. **B\_model\_infer/make\_fake\_predictions.py** `--kind dummy|oracle --profile base|paper --run-id <id>`: 기존 예측과 같은 스키마로 쓴다. as\_of 날짜와 H, N은 프로필을 따르고, 종목은 그날의 유니버스다. run\_id 기본값은 fake\_{kind}\_{profile}이다.
   - dummy: 각 샘플·스텝을 last\_close × exp(N(0, σ))로 만든다. σ는 설정의 seed로 고정한 난수와 함께 테스트 상수로 둔다.
   - oracle: horizon\_step h의 예측을 실제 미래 h번째 거래일의 가격으로 채운다. std가 0이 되지 않게 아주 작은 노이즈를 더한다.
   - 가격은 price\_basis 기준(raw 또는 raw × F)을 따른다.
3. **C\_signal/aggregate.py**: (as\_of\_date, ticker)별로 exp\_ret, exp\_ret\_mean, std, p\_up, pred\_range, n\_samples를 계산한다. 정의는 개요서 표를 따르고, pred\_close\[H\]는 horizon\_step = H인 행, exp\_ret\_mean은 모든 샘플·모든 스텝(1..H)의 pred\_close 평균 / last\_close − 1이다(논문 식 R\_{t→t+H}). last\_close는 as\_of\_date 종가이며 예측과 같은 가격 기준이다. H와 N은 run\_id의 manifest에서 읽고, 예측 파일에 있는 모든 as\_of 날짜에 대해 계산한다(프로필에 따라 주간 또는 매일).
4. **C\_signal/baseline\_features.py**: 수정가격으로 mom20(20일 수익률), vol20(일간 수익률 20일 표준편차), rev5(최근 5일 수익률의 음수)를 as\_of\_date까지의 데이터로만 계산한다.
5. **C\_signal/run\_signal.py** `--run-id`: 둘을 합쳐 data/C\_signals/{run\_id}/signals.parquet로 저장한다. meta.json에 run\_id의 profile, H, N을 복사해 둔다.

**산출물**: data/B\_predictions/fake\_{dummy,oracle}\_{base,paper}/ (4개), data/C\_signals/{run\_id}/signals.parquet, meta.json

**테스트** (tests/test\_stage2\_signal.py)

- 오라클 예측의 exp\_ret가 실제 H일 뒤 종가 수익률과, exp\_ret\_mean이 실제 1..H일 뒤 종가 평균 수익률과 (노이즈 범위 안에서) 같다
- paper 프로필의 가짜 예측은 as\_of가 매 거래일이고 horizon\_step이 1..10, 샘플이 10개다
- **미래 변경 불변 테스트**: as\_of\_date 이후 가격을 임의로 바꿔도 baseline\_features 결과가 바뀌지 않는다
- 시그널에 (as\_of\_date, ticker) 중복이 없고, 그날 유니버스 밖 종목이 없다
- p\_up은 0\~1, std와 pred\_range는 0 이상이다
- 기존 Kronos 예측 파일 하나를 aggregate에 넣어도 오류 없이 돈다

**완료 기준**

- [ ] 테스트 통과
- [ ] fake\_{dummy,oracle}\_{base,paper} 네 run\_id의 signals.parquet가 생성된다

**사용자 확인**: 리밸런싱일 목록(처음과 끝, 개수), 날짜별 시그널 종목 수, 오라클 시그널의 exp\_ret와 실현 수익률 산점도

## Stage 3. D\_strategy — 인터페이스와 벤치마크

**목표**: 모든 전략이 따를 인터페이스와 "전략 하나 = 파일 하나" 구조, 레지스트리, `--strategy` 인자를 만들고, 엔진 검증에 쓸 벤치마크 전략 3개를 구현한다. Kronos 전략은 Stage 6에서 만든다.

**선행 결정**: momentum20\_topk, random\_topk의 K

**작업**

1. **base.py**: 아래 인터페이스를 정의한다. weights는 시그널만 보고, 가격·라벨에는 접근하지 않는다.

```python
class Strategy(ABC):
    name: str                      # == 파일명 == cfg.strategies 키
    profile: str                   # 쓰는 예측의 추론 프로필 (cfg에서)
    schedule: str                  # "weekly" (H 간격) | "daily"
    def __init__(self, params: dict, seed: int): ...
    def reset(self) -> None: ...   # run_strategy가 시작할 때 호출. 상태를 가진 전략은 여기서 초기화
    @abstractmethod
    def weights(self, date, signals: pd.DataFrame, prev_w: pd.Series) -> pd.Series:
        """signals: 해당 date의 ticker × 시그널.
        prev_w: 직전 리밸런싱의 목표 비중 (실제 보유가 아님).
        반환: ticker → 목표 비중. 값 >= 0이고 NaN이 아닌 값의 합 <= 1 (나머지는 현금).
        NaN = 보류(hold): 그 종목은 거래하지 않고 현재 포지션을 유지하라는 뜻.
        전략은 날짜 오름차순으로 한 번씩만 호출되며 내부 상태를 가질 수 있다 (자기 과거 출력만으로)."""
```

2. **registry.py**: D\_strategy 폴더의 모듈(base, registry, run\_strategy 제외)을 자동으로 import해 `@register` 데코레이터가 붙은 클래스를 이름 → 클래스 dict에 모은다. get\_strategy(name, cfg)는 cfg.strategies.\<name>에서 profile, schedule, 파라미터를 읽어 인스턴스를 만든다. 클래스의 name이 파일명·설정 키와 다르면 import 시점에 오류를 낸다. resolve("a,b" | "all") → 이름 리스트도 여기 둔다.
3. 전략 파일 세 개 **equal\_weight.py, momentum20\_topk.py, random\_topk.py**를 만든다 (파일 하나에 전략 하나).

| 전략 | 규칙 |
| --- | --- |
| EqualWeight | 그날 시그널이 있는 모든 종목 동일가중 |
| Momentum20TopK | mom20 상위 K개 동일가중 |
| RandomTopK | seed로 고정한 난수 점수 상위 K개 동일가중. 날짜마다 다른 난수, 같은 seed면 같은 결과 |

4. **run\_strategy.py** `--run-id --strategy a,b|all`: 전략마다 (1) run\_id manifest의 profile이 전략의 profile과 같은지 확인하고 다르면 명확한 오류로 멈춘다, (2) 리밸런싱일을 schedule대로 정한다(weekly: rebalance\_dates(start, end, H), daily: 시그널이 있는 모든 거래일), (3) reset() 후 날짜 순서대로 weights를 호출해 data/D\_weights/{run\_id}/{strategy}.parquet(as\_of\_date, ticker, weight)로 저장한다. 비중이 0인 종목은 저장하지 않고, 보류 종목은 weight = NaN으로 저장한다.

지수 Buy&Hold는 종목 비중으로 표현하지 않는다. Stage 5에서 벤치마크 수익률 시계열로 처리한다.

**테스트** (tests/test\_stage3\_strategy.py)

- 모든 등록 전략에 대해(레지스트리에서 자동 수집): 비중 ≥ 0, NaN 제외 합 ≤ 1 + 1e-9, 그날 시그널에 없는 종목 없음, 입력 DataFrame을 바꾸지 않음, 같은 입력에 같은 출력
- 레지스트리: 파일명·클래스 name·설정 키가 일치하고, resolve("all")이 폴더의 전략 파일 수와 같다
- profile 불일치(예: base 전략을 paper run\_id에) 시 run\_strategy가 오류로 멈춘다
- TopK 계열은 정확히 min(K, 종목 수)개를 보유한다
- RandomTopK는 같은 seed에서 결과가 같고, 다른 seed에서 다르다
- 시그널 동점이 있을 때 결과가 실행마다 같다 (정렬 기준에 ticker를 보조 키로 사용)

**완료 기준**

- [ ] 테스트 통과
- [ ] fake\_dummy\_base run\_id로 `--strategy all`이 세 전략의 비중 파일을 생성한다

**사용자 확인**: 전략별 날짜당 보유 종목 수, 리밸런싱 사이 종목 교체율

## Stage 4. E\_backtest — 엔진 v1과 비용

**목표**: 비중 공간에서 동작하는 이상적 엔진 v1과 비용 모델을 만들고, 정답을 아는 입력으로 엔진의 배선을 증명한다.

**선행 결정**: sell\_tax\_table, delist\_policy (테스트는 테스트용 값으로 먼저 진행 가능)

**시간 규칙** (v1, v2 공통)

1. 리밸런싱일 s의 비중은 다음 거래일 f = next\_trading\_day(s)의 시가에 체결한다.
2. 가치 평가는 매일 종가로 한다. 체결일 f의 수익률은 두 조각이다. 전일 종가 → f 시가는 기존 보유, f 시가 → f 종가는 새 보유로 계산한다.
3. 리밸런싱 사이에는 수량을 고정한다. 그래서 비중은 가격을 따라 움직인다(drift). 다음 체결의 거래량은 drift된 비중과 목표 비중의 차이다.
4. 수익률은 수정가격(raw × F)으로 계산한다.

**작업**

1. **costs.py**: CostModel(cfg)에 buy\_cost\_rate(date, market), sell\_cost\_rate(date, market)를 둔다. 매수는 수수료 + 슬리피지, 매도는 수수료 + 슬리피지 + 거래세다. 거래세는 sell\_tax\_table에서 그 날짜에 유효한 시장별 세율을 고른다. 표가 비어 있으면 오류를 낸다.
2. **engine\_v1\_weights.py**: run\_v1(weights, prices, calendar, cost\_model, cfg) → dict(nav, daily, trades, holdings)를 순수 함수로 만든다. 날짜는 루프로, 종목 방향은 벡터 연산으로 처리한다.
   - 체결일: pre = drift된 비중, tgt = 목표 비중. 매수량 = Σ max(tgt − pre, 0), 매도량 = Σ max(pre − tgt, 0). 비용 = 종목별 매수량 × 매수 비용률 + 매도량 × 매도 비용률.
   - 체결일에 가격이 없는 종목(정지)은 v1에서도 거래하지 않고 기존 비중을 유지한다. 해당 건은 trades에 표시한다.
   - 목표 비중이 NaN(보류)인 종목은 거래하지 않고 drift된 비중을 유지한다(status `hold`). 매수 비중 합이 가용 비중(1 − 보류·유지 비중 합)을 넘으면 모든 매수를 같은 비율로 축소한다(status `scaled`). 스케줄(주간·매일)은 비중 파일의 날짜로만 드러나며 엔진에 분기가 없다.
   - 보유 중 상장폐지되면 delist\_policy대로 현금화한다.
   - 소수점 비중을 허용하고, 현금·가격제한·유동성 제약은 없다.
3. **run\_backtest.py** `--run-id --strategy a,b|all --engine v1 [--no-costs] [--costs kr|paper]`: D\_weights와 A\_prepared를 읽어 전략마다 실행하고 data/E\_backtest/{run\_id}/v1/{strategy}/에 저장한다. `--costs paper`는 costs.paper(매수 0.10%, 매도 0.15%)를 쓰고 폴더에 `@paper_costs`를 붙인다.

**산출물**

| 파일 | 컬럼 |
| --- | --- |
| nav.csv | date, nav, ret, cost |
| trades.parquet | fill\_date, signal\_date, ticker, w\_pre, w\_tgt, trade\_w, cost, status |
| holdings.parquet | date, ticker, weight (종가 기준) |
| meta.json | 공통 규칙 13 |

**테스트** (tests/test\_stage4\_engine\_v1.py)

- **장난감 예제**: 3종목·6거래일 가격과 비중을 테스트 안에 직접 만들고, 기대 NAV를 손계산해 주석과 함께 적는다. 엔진 결과가 1e-10 안에서 같다
- **비용**: 비용률 0이면 cost가 모두 0이고, 비용이 있으면 NAV 차이가 턴오버 × 비용률과 맞는다
- **오라클**: fake\_oracle 시그널로 exp\_ret 상위 K 전략(테스트 안에서만 정의)을 돌리면 CAGR이 EqualWeight보다 비정상적으로 높다. 같은 시그널을 하루 늦춰 체결하면 그 우위가 크게 줄어든다
- **랜덤**: RandomTopK를 여러 seed로 돌린 평균 CAGR이 EqualWeight 근처에 있다 (허용 범위는 테스트 상수, 근거를 주석으로)
- **lookahead**: 모든 trades에서 signal\_date < fill\_date (common.lookahead 사용)
- **drift**: 리밸런싱이 없는 구간에서 비중 합이 1을 유지하고, 개별 비중이 가격 비율대로 변한다
- **보류**: 매일 리밸런싱 날짜에 모든 보유 종목이 NaN이면 거래·비용이 0이고 NAV가 buy&hold와 같다. 일부만 NaN이면 그 종목의 비중이 drift 값 그대로다

**완료 기준**

- [ ] 테스트 통과
- [ ] fake\_dummy\_base의 세 벤치마크 전략이 `--strategy all`로 v1에서 끝까지 돈다

**사용자 확인**: 장난감 예제의 손계산 과정(직접 따라가 보기), 오라클과 오라클+1일의 누적수익 그래프, seed별 RandomTopK 성과 분포

## Stage 5. F\_evaluate — 채점

**목표**: 예측(시그널)과 포트폴리오(NAV)를 따로 채점하는 지표를 직접 구현하고, 유의성 검정을 붙인다.

**선행 결정**: 벤치마크 지수 (없으면 EqualWeight를 벤치마크로 사용)

**작업**

1. **labels.py**: 라벨은 이 폴더에서만 계산한다. 체결과 같은 기준으로 정의하고, H는 run\_id의 manifest(프로필)에서 읽는다(base 5, paper 10). exp\_ret\_mean의 채점용으로 1..H일 시가 평균 수익률 라벨도 함께 만든다.

```latex
\text{label}(s, i) = \frac{\text{Open}^{adj}_i(f + H)}{\text{Open}^{adj}_i(f)} - 1, \quad f = \text{next\_trading\_day}(s)
```

2. **signal\_metrics.py**: 아래 함수를 순수 함수로 만든다.
   - rank\_ic(signals, labels, col) → 날짜별 Spearman 상관, ic\_summary → 평균, 표준편차, ICIR, t값
   - quantile\_returns(signals, labels, col, q) → 날짜 × 분위 평균 라벨, Q5−Q1 스프레드
   - hit\_rate(signals, labels) → p\_up > 0.5와 실제 방향의 일치율
   - calibration(signals, labels) → std와 |label − exp\_ret|의 상관
   - naive 대조군: 랜덤워크(exp\_ret = 0이므로 IC 정의 불가 → 보고서에 명시), mom20, rev5의 IC
   - by\_regime(...) → 반기별, 시장 상승·하락 구간별 분해 (구간 정의는 벤치마크 수익률 부호)
3. **portfolio\_metrics.py**: nav.csv와 trades에서 계산한다.
   - 수익·위험: CAGR, 연변동성, Sharpe, Sortino, Calmar, MDD, MDD 회복기간, 월별 수익률 분포
   - 벤치마크 대비: 초과수익, 베타, 알파, Tracking Error, Information Ratio, 월별 hit ratio
   - 거래: 연평균 턴오버, 비용 전후 CAGR 차이(--no-costs 실행과 비교), 평균 보유종목 수, 하루 평균 교체 종목 수
   - 논문 지표 이름을 함께 쓴다: AER = 벤치마크 대비 연율화 초과수익, IR = Information Ratio. 벤치마크는 evaluate.benchmark(지수 티커 또는 equal\_weight)
   - 연율화 계수는 252, 무위험 수익률은 설정 키(evaluate.risk\_free, 기본 0)
4. **significance.py**: block\_bootstrap\_sharpe\_ci(ret, n, block, seed) → 95% 구간, deflated\_sharpe(sr, n\_trials, T, skew, kurt, sr\_var) (Bailey & López de Prado 2014 공식, 출처를 docstring에)
5. **run\_evaluate.py** `--run-id --strategy a,b|all`: 해당 run\_id의 시그널과 선택한 엔진·전략 결과를 채점해 data/F\_metrics/{run\_id}/에 저장한다. 실행마다 trials.csv에 (run\_id, engine, strategy, config\_hash, sharpe)를 추가하되, 같은 config\_hash는 한 번만 센다.

**산출물**: signal\_metrics.json, ic\_timeseries.csv, quantile\_returns.csv, portfolio\_metrics.csv (engine × strategy × 지표), trials.csv, meta.json

**테스트** (tests/test\_stage5\_metrics.py)

- 시그널 = 라벨이면 IC = 1, 시그널 = −라벨이면 IC = −1, 난수 시그널이면 IC 평균이 0 근처
- 합성 수익률 시계열에서 CAGR, Sharpe, Sortino, MDD가 quantstats와 허용 오차 안에서 같다
- 라벨 계산이 signal\_date 다음날 시가에서 시작한다 (as\_of 당일 정보가 섞이지 않는다)
- deflated\_sharpe가 n\_trials 증가에 따라 감소한다
- 같은 seed의 부트스트랩 결과가 같다

**완료 기준**

- [ ] 테스트 통과
- [ ] fake\_oracle과 fake\_dummy의 지표가 생성되고, 오라클 IC가 1에 가깝고 더미 IC가 0에 가깝다

**사용자 확인**: 두 가짜 run\_id의 IC 요약표, 분위별 수익률 막대그래프, 벤치마크 전략 성과표

## Stage 6. Kronos 전략과 실제 예측 연결

**목표**: Kronos 전략 3개를 구현하고, 실제 예측 두 개(base 프로필 kronos\_base\_v1, paper 프로필 kronos\_paper\_v1)로 C → D → E(v1) → F를 처음 끝까지 돌린다. TopK는 원 논문 투자 시뮬레이션의 재현이다(부록 C). 이 Stage에서 처음으로 Kronos 결과를 본다.

**선행 결정**: TopK의 유니버스·k·n 대응안(개요서 결정 사항), vol\_target의 K, conf\_weighted의 임계치, 논문 재현의 벤치마크 지수, paper 프로필 추론 실행 여부(RunPod 비용), 실제 예측이 price\_basis 설정과 같은 기준으로 만들어졌는지

**작업**

1. **D\_strategy/topk.py** (논문 재현, profile paper, schedule daily, signal\_col exp\_ret\_mean): Qlib TopkDropoutStrategy(topk = k, n\_drop = n, hold\_thresh = hold\_min\_days, method\_sell bottom, method\_buy top)의 규칙을 따른다. 상태는 보유 집합과 종목별 보유 일수다.
   - 매일: 보유 종목을 signal\_col로 정렬한 `last`, 미보유 종목 상위 `n + k − |last|`개인 `today`, 둘을 합쳐 정렬한 `comb`를 만든다.
   - 매도 = comb의 하위 n개에 든 보유 종목 중 보유 일수 ≥ hold\_min\_days인 것. 미달 종목은 팔지 않는다.
   - 매수 = today 상위에서 `|매도| + k − |last|`개. 각 1/k 비중.
   - 반환: 매수 종목 1/k, 매도 종목 0(= 미포함), 남은 보유 종목 NaN(보류). 첫날은 상위 k개를 1/k씩 산다.
   - 동점은 ticker를 보조 키로 정렬한다. 거래일마다 보유 일수를 1 늘린다.
2. **D\_strategy/conf\_weighted.py**: score = exp\_ret / std. score가 임계치 이상인 종목만 score에 비례해 가중하고, 해당 종목이 없으면 전액 현금
3. **D\_strategy/vol\_target.py**: exp\_ret 상위 K를 1 / pred\_range에 비례해 가중
4. **B\_model\_infer/validate\_predictions.py**: 실제 예측 폴더를 검사해 보고서용 요약을 만든다.
   - manifest의 profile과 lookback, pred\_len, step, sample\_count, T, top\_p가 infer.profiles.\<profile>과 일치하는가
   - as\_of\_date 목록이 rebalance\_dates(start, end, step)와 일치하는가 (빠진 날짜 목록)
   - 날짜별 종목이 그날 유니버스와 얼마나 겹치는가 (빠진 종목 수)
   - horizon\_step이 1..H, 종목당 샘플 수가 sample\_count인가
   - &#91;확인 필요\] 예측의 가격 수준이 as\_of 종가의 원주가와 수정주가 중 어디에 가까운가 (분할 이력이 있는 종목으로 확인)
5. RunPod에서 paper 프로필 추론을 돌린다: `RUN_ID=kronos_paper_v1 PROFILE=paper bash RunPod/runpod.sh` (부록 C-2). 유니버스는 선행 결정의 대응안을 따른다(코스피 시총 상위 200이면 `universe.top_n_mktcap: 200`으로 Stage 1 universe를 다시 만들어 별도 run\_id로).
6. 실제 run\_id 두 개로 `scripts/run_pipeline.py --run-id kronos_base_v1 --strategy equal_weight,momentum20_topk,random_topk,conf_weighted,vol_target`와 `--run-id kronos_paper_v1 --strategy topk,equal_weight,momentum20_topk,random_topk`를 실행한다(run\_pipeline은 Stage 8에서 만들므로 그 전에는 단계 CLI를 손으로 순서대로 실행). TopK는 `--costs kr`과 `--costs paper` 둘 다 돌린다.

**테스트** (tests/test\_stage6\_kronos.py)

- 세 전략이 Stage 3의 공통 비중 제약 테스트를 통과한다 (등록만 하면 자동 포함)
- **TopK (tests/test\_topk\_dropout.py)**: 합성 시그널 30일에서 (1) 보유 수가 항상 k(종목 수가 충분할 때), (2) 하루 매도·매수 각각 ≤ n, (3) 보유 일수 < hold\_min\_days인 종목은 팔리지 않음, (4) 첫날 이후 반환값에 NaN(보류)이 있고 NaN 제외 합 ≤ 1, (5) 시그널이 바뀌지 않으면 교체 0, (6) 같은 입력에 같은 출력
- TopK 비중 파일을 v1에 넣으면 trades의 hold 건수가 보류 종목 수와 같고, 매일 거래 종목 수 ≤ 2n
- conf\_weighted가 모든 score가 임계치 미만인 날 전액 현금이다
- vol\_target에서 pred\_range가 0인 종목이 있어도 오류 없이 처리한다 (처리 규칙을 보고서에)

**완료 기준**

- [ ] 테스트 통과
- [ ] validate\_predictions 요약이 보고서에 있고, 불일치가 있으면 원인 추정이 적혀 있다
- [ ] kronos\_base\_v1로 5개 전략(TopK 제외), kronos\_paper\_v1로 TopK + 벤치마크 3개의 v1 결과와 지표가 생성된다
- [ ] TopK의 설정이 부록 C의 대조표와 일치함을 보고서에 표로 보인다(논문 값, 한국 적용 값, 차이와 이유)

**사용자 확인**: Kronos exp\_ret·exp\_ret\_mean의 IC 요약과 naive 대조군 비교(run\_id별), 분위별 수익률, 전략 6개의 비용 전후 성과표(TopK는 kr·paper 비용 둘 다, AER·IR 포함), 누적수익 그래프, TopK의 일별 교체 종목 수 분포. 결과를 보고 파라미터를 바꿔 다시 돌린다면 trials.csv에 기록되는지 확인한다.

## Stage 7. E\_backtest — 엔진 v2와 현실 제약

**목표**: 현금과 보유 주식 수를 상태로 들고 주문 단위로 체결하는 엔진 v2를 만든다. 제약 6종을 설정으로 하나씩 켜고 끌 수 있어야 한다.

**선행 결정**: init\_cash, price\_limit\_pct, tick\_table, max\_participation, delist\_policy

**하루의 처리 순서** (engine\_v2\_orders.py)

1. **기업행위**: ex-date인 보유 종목은 주식 수에 r\_t를 곱한다 (분할·무상증자는 정확, 유상증자는 가치 보존 근사로 보고서에 한계 명시).
2. **상장폐지**: delist\_policy대로 현금화한다.
3. **체결일이면 주문 생성**: 시가 기준 NAV × 목표 비중 / 원주가 시가로 목표 주식 수를 정한다. 차이만큼 매도·매수 주문을 만든다.
4. **매도 먼저**: 각 주문에 제약을 적용하고 원주가 시가 × (1 − 슬리피지)로 체결한다. 대금에서 수수료·거래세를 뺀다.
5. **매수**: 남은 현금 안에서 체결한다. 원주가 시가 × (1 + 슬리피지), 수수료 차감.
6. **평가**: 원주가 종가 × 보유 주식 수 + 현금으로 NAV를 계산한다. 거래정지 종목은 직전 가격으로 평가한다.

미체결 주문은 다음 리밸런싱까지 다시 내지 않는다. 기존 보유를 그대로 유지하고 status로 기록한다.

**제약별 규칙** (모두 constraints.py의 순수 함수)

| 제약 | 끄면 | 켜면 | status |
| --- | --- | --- | --- |
| costs | 비용 0 | Stage 4의 CostModel | — |
| integer\_shares | 소수점 주식 허용 | 목표 주식 수를 내림 | — |
| cash | 현금이 음수가 되어도 체결 | 매수 총액이 현금을 넘으면 모든 매수를 같은 비율로 축소 후 다시 내림 | scaled\_cash |
| price\_limit | 무시 | 시가 ≥ 상한가면 매수 거부, 시가 ≤ 하한가면 매도 거부. 기준가는 전일 원주가 종가(ex-date는 r\_t로 나눔), 상·하한가는 tick\_table로 반올림 | rejected\_limit |
| halt | 무시 (v1과 같게 가격 없으면 유지) | 정지 종목은 주문 거부 | rejected\_halt |
| liquidity | 무시 | 주문 금액을 당일 거래대금 × max\_participation 이하로 축소 | partial\_liquidity |

**산출물**: data/E\_backtest/{run\_id}/v2/{strategy}/{scenario}/ 아래 nav.csv, trades.parquet(date, signal\_date, ticker, side, target\_shares, filled\_shares, price, value, commission, tax, slippage, status), positions.parquet(date, ticker, shares, close, value, cash), meta.json. scenario는 켜진 제약 조합의 이름이다 (예: all\_off, costs, costs+integer\_shares).

**테스트** (tests/test\_stage7\_engine\_v2.py)

- **일치성**: 모든 제약을 끈 v2가 v1과 일간 수익률 기준 허용 오차 안에서 같다 (fake\_dummy, 세 벤치마크 전략)
- **장난감 예제**: 정수 수량과 현금 제약을 켠 3종목 예제를 손계산해 일치시킨다
- **분할**: 1:5 분할일 전후로 보유 가치가 변하지 않는다
- **가격제한**: 시가가 상한가인 종목의 매수가 거부되고, 그 비중만큼 현금이 남는다
- **거래정지**: 정지 종목 주문이 거부되고 가치는 직전 가격으로 유지된다
- **불변식**: 현금 제약이 켜져 있으면 현금 ≥ 0, 보유 주식 수 ≥ 0, 정수 제약이 켜져 있으면 주식 수가 정수
- **lookahead**: 모든 trades에서 signal\_date < date

**완료 기준**

- [ ] 테스트 통과
- [ ] 실제 run\_id로 shortfall\_order의 누적 시나리오 7개(all\_off, 그리고 하나씩 추가)가 shortfall\_strategy에 대해 생성된다

**사용자 확인**: status별 거부·축소 건수 표, 가격제한으로 거부된 주문 샘플 몇 건(날짜·종목·시가·상한가), all\_off v2와 v1의 NAV 겹친 그래프

## Stage 8. G\_report — 비교와 분해

**목표**: 명령 하나로 전략 비교 리포트(run\_id 여러 개에 걸쳐)와 shortfall 분해 리포트를 만들고, C→G를 한 번에 돌리는 run\_pipeline을 만든다.

**선행 결정**: shortfall\_strategy, shortfall\_order (기본값 유지 가능)

**작업**

1. **compare.py**: report.compare\_run\_ids(또는 `--run-id a,b`)의 F\_metrics를 읽어 compare.md를 만든다.
   - 시그널 평가: run\_id마다 Kronos exp\_ret·exp\_ret\_mean과 naive 대조군의 IC 요약표, 분위별 수익률 그림 (H가 다르므로 run\_id 간에는 합치지 않는다)
   - 전략 비교: 행 = `{strategy}@{run_id}`(비용 시나리오가 다르면 `@paper_costs` 추가), 열 = CAGR, AER, Sharpe, MDD, IR, 턴오버, 하루 평균 교체 수, 비용 전후 차이, Sharpe 95% 구간, Deflated Sharpe. 모든 행은 같은 기간·같은 벤치마크여야 하며 다르면 표 아래 주석으로 적는다
   - 누적수익 그림 (v1 비용 포함, 벤치마크 포함, run\_id 구분 가능한 선 스타일)
   - `--strategy a,b|all`로 행을 고를 수 있다
2. **shortfall.py**: Stage 7의 누적 시나리오 결과로 shortfall.md를 만든다.
   - 표: 시나리오, CAGR, Sharpe, 직전 대비 ΔCAGR, ΔSharpe
   - 워터폴 그림: v1(비용 없음)에서 시작해 제약을 하나씩 켤 때의 CAGR 감소
   - 분해 순서가 결과에 영향을 준다는 점과 사용한 순서를 본문에 적는다
   - v1(비용 없음)과 v2 all\_off의 차이를 "엔진 차이"로 별도 표시한다 (0에 가까워야 함)
3. **run\_report.py** `--run-id a,b [--strategy ...]`: 1, 2를 실행해 reports/{첫 run\_id 또는 compare 이름}/compare.md, shortfall.md, figures/\*.png를 만든다. 그림은 matplotlib로 그린다.
4. **scripts/run\_pipeline.py** `--run-id <id> --strategy a,b|all [--engine v1|v2] [--costs kr|paper] [--from C] [--to G]`: run\_signal → run\_strategy → run\_backtest → run\_evaluate → run\_report를 subprocess(`python -m ...`)로 순서대로 호출한다. 단계가 실패하면 거기서 멈추고 어느 단계인지 출력한다. 단계 패키지를 import하지 않는다.

**테스트** (tests/test\_stage8\_report.py)

- 워터폴의 단계별 ΔCAGR 합이 처음과 끝 시나리오의 CAGR 차이와 같다
- fake\_dummy\_base와 fake\_dummy\_paper 두 run\_id를 함께 넣어 리포트 생성이 오류 없이 끝나고, compare.md에 두 run\_id의 전략 행이 모두 있으며 그림 파일이 생긴다 (스모크 테스트)
- run\_pipeline이 fake\_dummy\_base에서 `--strategy equal_weight`로 C→G를 끝까지 돌린다
- 리포트의 모든 수치가 F\_metrics 파일에서 온다 (리포트 코드에서 지표를 다시 계산하지 않는다)

**완료 기준**

- [ ] 테스트 통과
- [ ] 실제 run\_id 두 개(kronos\_base\_v1, kronos\_paper\_v1)로 compare.md 한 장과 shortfall.md가 생성된다

**사용자 확인**: 두 리포트 전체. 특히 shortfall 워터폴에서 가장 큰 감소를 만든 제약이 무엇인지, 그 결과가 직관과 맞는지 확인한다.

## 부록 A. 유지한 기존 코드 (그대로 쓰는 유틸)

사전 정리 후 레포에 남아 있는 코드다. 공개 함수의 이름과 동작은 유지하고, 명세의 이름이 다르면 alias를 추가한다. Stage 0에서 패키지 위치만 바뀐다(`common/`은 그대로, `data_prepare/` → `A_data_prepare/`, `infer/` → `B_model_infer/`).

| 모듈 | 공개 함수 | 명세와의 대응 / 주의 |
| --- | --- | --- |
| common/config.py | load\_config(path), cfg\_get(cfg, "a.b.c", default), cfg\_override(cfg, {dotted: value}) (None은 무시, 깊은 복사), dump\_config(cfg, path) | Stage 0에서 config\_hash(cfg), require(cfg, key)를 추가한다. cfg\_get/cfg\_override는 data\_prepare와 infer가 쓴다 |
| common/paths.py | class Paths(cfg, root): raw\_dir(), price\_file(index), universe\_dir(), constituents\_file(index, variant=None → cfg universe.variant), krx\_cache\_dir(snapshot), logs\_dir(), predictions\_dir(run\_id), prediction\_file(run\_id, as\_of) → as\_of=YYYY-MM-DD.parquet, manifest\_file(run\_id), results\_dir(run\_id, strategy); as\_of\_from\_filename(path) | data\_prepare/run.py와 sanity.py가 Paths를 쓰므로 메서드명 유지. Stage 0에서 prepared\_path, signals\_path, weights\_path, backtest\_dir, metrics\_dir, report\_dir를 추가하고 results\_dir는 제거한다 |
| common/lookahead.py | LookaheadError(AssertionError); assert\_no\_future(df, as\_of, date\_col="date"); slice\_as\_of(df, as\_of, date\_col) → date ≤ as\_of 행만 반환 후 재검사; assert\_signal\_before\_fill(rebalance\_dates, fill\_dates) → 하나라도 as\_of ≥ fill이면 예외 | 명세의 assert\_no\_future\_rows ≡ assert\_no\_future, assert\_fill\_after\_signal(trades) ≡ assert\_signal\_before\_fill(trades.signal\_date, trades.fill\_date). alias로 추가 |
| common/schema.py | PREDICTION\_COLUMNS(dtype 포함), empty\_predictions(), coerce\_predictions(df), validate\_predictions(df, as\_of=None, horizon=None) → 컬럼·dtype·horizon 범위·as\_of 일치·(as\_of, ticker, step, sample) 유일·유한값 검사, predictions\_from\_array(as\_of, tickers, samples[n\_t, n\_s, H, 5]) | 예측 스키마는 새 설계와 동일하므로 그대로 쓴다. Stage 0에서 가격·시그널·비중·NAV의 validate\_\*를 같은 스타일로 추가 |
| common/data.py | PRICE\_COLUMNS; load\_prices(cfg, root, indices=None, include\_benchmark=True) → data/raw/{index}/prices.parquet 결합 + index/market 컬럼; normalize\_prices(df) (날짜 정규화, ticker str, adj\_factor 결측 1, (ticker, date) 중복 제거); to\_wide(prices, col); ticker\_market\_map; trading\_calendar(prices); rebalance\_dates(calendar, start, end, step) → start 이상 첫 거래일부터 step번째마다; ffill\_wide(wide, limit); adjusted\_wide(prices, col, ffill\_limit) → raw × adj\_factor | infer가 쓴다. 라벨 함수(forward\_returns, open\_to\_open\_returns)는 원칙에 따라 제거했다. 명세의 common/calendar.rebalance\_dates(start, end, H)는 이 정의와 같은 날짜를 내야 run\_inference와 맞는다 |
| common/universe.py | load\_constituents(cfg, root, indices), normalize\_constituents(df), universe\_at(constituents, date) → date 이하 최신 스냅샷의 종목 리스트(없으면 []), combined\_universe\_at({index: cons}, date) → DataFrame(ticker, index), membership\_matrix(cons, dates), all\_tickers(cons, start, end) | 명세의 get\_universe(date) ≡ universe\_at. 거래소 결합은 combined\_universe\_at |
| common/synthetic.py | synthetic\_prices(tickers, start, end, seed, daily\_vol, drift, split\_prob, holiday\_prob, calendar) → GBM 원주가 + adj\_factor(무작위 2:1 분할, 임의 휴장); synthetic\_constituents; synthetic\_dataset(cfg, n\_per\_index, seed); write\_synthetic\_dataset(cfg, root) | 테스트 전용(tests/conftest.py). cfg의 universe.indices, universe.markets, benchmark.index\_tickers를 읽으므로 Stage 0에서 설정을 재구성하면 함께 고친다 |
| infer/build\_batch.py | Batch(as\_of, tickers, df\_list, x\_timestamps, y\_timestamps, last\_close, skipped); build\_batch(prices, tickers, as\_of, lookback, horizon, max\_stale\_days=5) | **가격 기준**: 윈도우를 slice\_as\_of로 자른 뒤 adj\_factor / adj\_factor[마지막 행]으로 rebase한다. 윈도우 안의 분할은 사라지고 마지막 종가는 as\_of 원주가와 같다. 즉 예측은 "as\_of 원주가 스케일"이고 C\_signal의 last\_close는 as\_of 원주가다(price\_basis: raw). 거래량은 factor로 나누고 amount = close × volume. 이력 부족·stale(as\_of와 마지막 봉 간격 > max\_stale\_days)·NaN·0 이하 가격 종목은 skipped에 사유 기록 |
| infer/backends.py | DummyBackend(seed, daily\_vol, signal\_strength=0, prices=None).predict(batch, horizon, sample\_count) → (n, S, H, 5) 랜덤워크 샘플; KronosBackend(model\_name, tokenizer\_name, revision, kronos\_repo, device, max\_context, temperature, top\_p, top\_k, batch\_size, seed) | Kronos는 sample\_count 샘플을 내부 평균하므로 KronosBackend는 각 시계열을 sample\_count번 복제해 sample\_count=1로 호출한다(원시 샘플 보존). DummyBackend의 signal\_strength > 0은 미래 수익률을 drift에 섞는 누수 모드다. Stage 2의 dummy는 DummyBackend(signal\_strength=0)로 만들 수 있고, oracle(스텝별 실제 미래 가격)은 따로 만든다 |
| infer/run\_inference.py | run(cfg, run\_id, backend, root, prices=None, constituents=None, log=print, extra\_manifest=None) → 쓴 날짜 목록; make\_backend(cfg, name, prices); git\_commit(root); write\_manifest(path, dict) (원자적); atomic\_parquet(df, path); CLI main | 재개 가능(기존 as\_of 파일 건너뜀), manifest.json에 모델·revision·lookback·pred\_len·T·top\_p·sample\_count·유니버스·커밋·skipped\_by\_date 기록. git\_commit·write\_manifest·atomic\_parquet는 common/meta.py로 옮길 후보 |
| data\_prepare/ | run.py(단계 오케스트레이터), krx\_client.py(KRXClient, ENDPOINTS, 캐시), transform.py(stock\_prices, detect\_reference\_price\_events, compute\_adj\_factor, filter\_securities, to\_project\_format), universe.py(build\_constituents), benchmark.py(index\_prices, etf\_prices), sanity.py(run, 게이트), validate.py(validate\_dataset), manifest.py(write, verify), env.py(load\_api\_key), log.py | docs/data\_pipeline.md가 설명한다. 다시 만들지 않는다 |
| tests/ | test\_data\_prepare.py (수집 파이프라인 단위 테스트), test\_no\_lookahead.py (slice\_as\_of·assert 가드, build\_batch의 과거 행만 사용·미래 변경 불변·분할 rebase, point-in-time 유니버스, 예측 파일 as\_of 일치, 재개), conftest.py (합성 데이터 fixture) | Stage 0 이후에도 통과해야 한다 |

## 부록 B. 삭제한 코드 중 참고할 구현

사전 정리에서 기존 `backtest/`, `scripts/`, 관련 테스트를 삭제했다. 새 엔진·지표는 학습 목표에 따라 직접 구현하되, 막히면 아래 커밋의 구현을 참고한다. 복사해 오면 보고서의 "명세서와 달라진 점"에 적는다.

```bash
git show b903c12:backtest/metrics.py        # 파일 하나 보기
git show b903c12 --stat                      # 삭제 전 파일 목록
```

| 삭제한 모듈 | 참고할 내용 | 쓰이는 Stage |
| --- | --- | --- |
| backtest/metrics.py | cagr, ann\_vol, sharpe, sortino, drawdown\_series, max\_drawdown(peak·trough·recovery·회복일수), calmar, monthly\_returns·monthly\_distribution, relative\_metrics(초과수익·베타·알파·TE·IR·월별 hit), trading\_metrics(연 턴오버·평균 보유·비용 전후 CAGR), block\_bootstrap\_sharpe\_ci(원형 블록 부트스트랩: 시작점을 n×⌈T/block⌉개 뽑아 블록 인덱스를 이어 붙이고 T개로 자름), probabilistic\_sharpe\_ratio(skew·kurtosis 보정 PSR), deflated\_sharpe\_ratio(기대 최대 SR = √V × [(1−γ)Φ⁻¹(1−1/N) + γΦ⁻¹(1−1/(N·e))], γ = 오일러 상수; sr\_variance 없으면 (1 + SR²/2)/T), rank\_ic\_series(날짜별 Spearman, 최소 표본 수), ic\_summary(mean·std·ICIR·t), quantile\_returns(날짜별 qcut 후 평균, Q5−Q1 스프레드와 t값), direction\_accuracy, calibration(std vs |오차| Spearman), naive\_comparison, regime\_labels(반기·상승/하락), strategy\_by\_regime | 5 (F\_evaluate). quantstats로 교차 검증 |
| backtest/costs.py | MarketCost(commission, sell\_tax, slippage): buy\_rate = commission + slippage, sell\_rate = commission + sell\_tax + slippage; CostModel(market\_costs, ticker\_market, default\_market).buy\_rates(tickers)/sell\_rates(tickers) | 4 (E costs.py). 날짜별 세율표는 없었으므로 새로 넣는다 |
| backtest/engine.py | run\_backtest(W[as\_of × ticker], open\_raw, adj\_factor, cost\_model, ffill\_limit): 체결일 = 캘린더에서 as\_of 다음 거래일(searchsorted right), 리밸런싱 사이 수량 고정(drift), 체결일 가격 없는 종목은 현금, 보유 중 가격 소실은 마지막가 청산, 턴오버 = Σ|Δw|/2, 비용은 매수·매도 금액 × 비용률; BacktestResult(nav, nav\_gross, returns, turnover, n\_holdings, costs, weights, fill\_dates, untradable) | 4 (E engine\_v1). **차이**: 구 엔진은 매일 시가로 평가(시가→시가 수익률)했고, 명세 v1은 종가 평가 + 체결일 두 조각 수익률이다. 구조만 참고 |
| backtest/signals.py | load\_predictions(파일명 as\_of == 컬럼 as\_of 검사), last\_close\_at(원주가 종가 ffill), prediction\_signals(exp\_ret, std, p\_up, pred\_range, n\_samples; horizon\_step == H 행만), price\_features(mom20, vol20, rev5; as\_of 이하만), realized\_returns(ret\_cc, ret\_oo; 라벨) | 2 (C\_signal), 5 (F labels.py) |
| backtest/strategies/ | base.Strategy.finalize(w, cap)(음수 제거, cap 적용, 합 > 1이면 정규화)·equal(tickers, exposure); 레지스트리(register 데코레이터, KRONOS/BENCHMARK 집합, uses\_predictions); TopK(k, signal, exposure), ConfidenceWeighted(threshold, max\_weight, min\_exp\_ret; score = exp\_ret/std), VolTarget(k, target\_range, range\_floor; 1/pred\_range 가중), EqualWeight, Momentum20TopK, RandomSignal(날짜별 난수: seed + 날짜 해시), IndexBuyHold(prev\_w 유지) | 3, 6 (D\_strategy). 동점 처리에 ticker 보조 키는 없었다 |
| backtest/report.py, scripts/compare.py | compare\_table(지표 dict → 표), plot\_compare(누적 NAV), \_jsonable(numpy·Timestamp → JSON), dsr\_cross(전략 수를 시도 횟수로, 전략 간 SR 분산을 sr\_variance로 쓴 DSR) | 8 (G\_report) |
| scripts/make\_dummy\_predictions.py | DummyBackend + run\_inference.run으로 더미 예측을 쓰고 manifest에 leaky 플래그·note를 남기는 흐름 | 2 (make\_fake\_predictions.py) |
| tests/test\_engine\_sanity.py | 오라클(실현 ret\_oo를 exp\_ret로 → EqualWeight 대비 비정상 수익·Sharpe > 4; 한 기간 늦추면 사라짐), 랜덤(여러 seed 평균이 EW와 비용 차이만큼만 다름), 단일 종목 buy&hold = 수정 시가 비율, 시장별 비용 손계산 일치, drift 손계산, 체결 불가 → 현금·가격 소실 → 청산 | 4 (test\_stage4) |
| tests/test\_strategies.py | 모든 등록 전략에 대해 비중 ≥ 0·합 ≤ 1·입력 불변·결정적, 빈 시그널 → 현금, TopK는 단조 변환 불변, ConfidenceWeighted 전액 현금 조건, RandomSignal seed·날짜별 차이 | 3 (test\_stage3) |


## 부록 C. RunPod 추론 운영 (실제 예측 생성)

실제 Kronos 예측(`kronos_base_v1`)은 RunPod **Secure Cloud**의 RTX 4090 Pod를 생성 작업 동안만 빌려 만들고, 결과를 로컬로 회수한 뒤 반납한다. 로컬 환경에는 외장 GPU가 없어 CPU로 전체를 생성하는 것은 현실적이지 않다. 운영 스크립트와 절차서는 `RunPod/`에 있다(RunPod/README.md, D-18). 이 부록은 Stage 6의 선행 조건이며, Stage 0 이후 언제든 진행할 수 있다.

**원칙**

1. 예측은 한 번 생성하면 고정된 빈티지로 취급한다. 같은 run\_id 폴더를 덮어쓰지 않고, 다시 생성해야 하면 새 run\_id를 쓴다. GPU 샘플링은 seed를 고정해도 GPU·드라이버·CUDA·torch 버전에 따라 비트 단위로 같다는 보장이 없기 때문이다.
2. GPU 시간은 생성에만 쓴다. 코드 오류는 로컬 CPU 스모크 테스트에서 먼저 잡는다.
3. 출력은 Pod가 사라져도 남는 저장소(/workspace에 연결한 네트워크 볼륨)에 쓴다. 예측 parquet은 SSH(rsync)로 로컬에 회수하고 GitHub에 올리지 않는다. 로컬에서 체크섬 대조가 끝나기 전에는 Pod를 terminate하지 않는다. Pod는 스스로 종료하지 않고, 종료는 로컬 `RunPod/local.sh terminate`가 verify 통과를 확인한 뒤에 한다.
4. Stage 6 전에는 예측의 성과(IC, 분위 수익률, 전략 수익)를 보지 않는다. 이 부록에서는 구조 검증만 한다. 엔진과 지표가 Kronos 결과를 본 뒤 조정되는 것을 막기 위해서다.
5. 실행 환경과 시간은 manifest에 남긴다. 결과가 달라졌을 때 원인을 추적할 수 있어야 한다.
6. 자격증명은 Secure Cloud Pod에만 둔다(D-18). Pod에 넣는 것은 이 레포에만 쓰기 권한이 있는 fine-grained `GITHUB_TOKEN`과 SSH 공개키 `PUBLIC_KEY`다. Community Cloud(제3자 호스트)는 쓰지 않는다. RunPod API 키는 Pod에서 쓰지 않고 로컬에 둔다. Kronos 코드와 모델은 공개 저장소에서 받으므로 HF 토큰은 두지 않는다.
7. 코드는 git으로, 데이터는 SSH로 움직인다. Pod는 레포를 clone해 돌고(새 run\_id는 최신 origin/main에서 시작), Pod의 커밋은 `results/<run_id>` 브랜치에만 가며 실행 메타데이터(manifest.json, checksums.json, cloud\_run.json, requirements.lock.txt)만 담는다. Pod에서는 코드를 고치지 않는다.

**선행 결정** (configs/base.yaml. 기존 추론 키가 있으면 그 이름과 위치를 따른다)

```yaml
infer:
  model_name: NeoQuasar/Kronos-base            # 기존 설정 값이 있으면 그 값을 따른다
  tokenizer_name: NeoQuasar/Kronos-Tokenizer-base
  revision: null             # [사용자] 모델 HF 커밋 해시. main 같은 브랜치 이름은 쓰지 않는다
  tokenizer_revision: null   # [사용자] 토크나이저 HF 커밋 해시
  kronos_repo_commit: null   # [사용자] Kronos 코드 저장소 커밋 해시
  lookback: 400
  pred_len: 5
  temperature: 1.0
  top_p: 0.9
  sample_count: 20           # [사용자] 20 유지 또는 상향 (선택 2)
  dtype: float32             # 기본값 유지. bf16은 수치가 달라지므로 쓰지 않는다
  batch_size: null           # Pod probe 결과로 기입
  universe_variant: null     # [사용자] base | liq5 (선택 1)
```

- **선택 1. 추론 유니버스**: 유니버스 필터는 거래대금 기준만 다르므로 날짜마다 base ⊆ liq5이고, clean ⊆ base다. liq5로 추론하면 세 변형을 재추론 없이 실험할 수 있다(종목 수 약 1.3배). 이 경우 C\_signal이 백테스트용 `universe.variant`의 유니버스로 종목을 거르도록 Stage 2의 run\_signal을 보강한다(Stage 2 완료 기준 "그날 유니버스 밖 종목 없음"과 일치).
- **선택 2. 샘플 수**: sample\_count를 20보다 크게(예: 50) 뽑으면, 앞에서부터 20개만 쓴 결과가 현 설계와 같은 20샘플이 되고 샘플 수 민감도를 재추론 없이 볼 수 있다. 이 경우 `signal.n_samples: 20` 키를 추가하고 C\_signal/aggregate가 sample\_id 앞에서부터 n\_samples개만 쓰게 한다. 생성 시간은 샘플 수에 비례한다.
- Kronos 프로젝트의 다른 주제에 다른 pred\_len이나 날짜 간격의 예측이 필요하면, 같은 Pod 세션에서 별도 run\_id로 생성한다.

### C-1. 로컬 준비 (에이전트 작업)

**요청 문구**

```text
docs/outline.md, docs/spec.md의 "부록 C", RunPod/README.md, docs/stage_reports/ 아래 이전 보고서를 읽어줘.
그다음 부록 C의 "C-1. 로컬 준비"만 구현해. Pod 생성이나 네트워크 업로드는 하지 마.
명세서의 공통 규칙을 지키고, 수정·호출할 기존 코드(B_model_infer/)를 먼저 읽어.
명세와 실제 레포가 다르면 레포 기준으로 맞추고 보고서에 적어.
끝나면 pytest 전체와 CPU 스모크 테스트를 돌리고,
docs/stage_reports/pod_prep.md를 단계 보고서 형식으로 작성한 뒤 "pod_prep: <요약>"으로 커밋하고 멈춰.
```

**작업**

1. **B\_model\_infer/env\_info.py**: `collect_env(device) → dict`. 키는 gpu\_name, gpu\_count, driver\_version, cuda\_version, torch\_version, python\_version, dtype, hostname, pod\_id(환경변수 `RUNPOD_POD_ID`가 있으면). CPU에서는 GPU 항목을 None으로 둔다. run\_inference의 `extra_manifest`로 넘긴다.
2. **manifest 보강**: 날짜 파일을 쓸 때마다 `env_by_date[as_of]`(gpu\_name, driver\_version, hostname)와 `elapsed_by_date[as_of]`(초)를 기록하고, 전체 시작·종료 시각을 남긴다. 중단 후 다른 호스트에서 재개된 날짜를 구분하기 위해서다.
3. **B\_model\_infer/pod\_inputs.py** `write|list|verify`: Pod에 필요한 입력 데이터의 sha256 목록을 `RunPod/inputs.sha256.json`(커밋함, 경로는 common/paths.py의 `pod_inputs_file`)으로 관리한다.
   - 입력 = data/raw/{kospi,kosdaq}/prices.parquet + 추론 프로필들의 universe\_variant(선택 1)에 해당하는 유니버스 파일. data/krx\_raw와 단계 산출물은 넣지 않는다.
   - write: 목록(파일명, 바이트, sha256)을 쓴다. list: 레포 기준 상대 경로를 출력한다(rsync `--files-from`용). verify `[--src DIR]`: 파일을 목록과 대조하고 불일치하면 비정상 종료한다.
   - 코드는 git clone으로 가므로 묶지 않는다. 코드 커밋은 Pod가 cloud\_run.json의 `code_commit`에 남긴다.
4. **B\_model\_infer/checksum.py** `write|verify --run-id`: data/B\_predictions/{run\_id}/checksums.json(파일명, 바이트, sha256)을 쓰고 대조한다. 파일 개수나 해시가 다르면 비정상 종료한다. 예측 parquet은 커밋하지 않고 메타데이터만 결과 브랜치를 거쳐 커밋한다.
5. **B\_model\_infer/verify\_run.py** `--run-id`: as\_of 파일 수가 manifest의 리밸런싱일 수(예측 가능 종목이 없던 날 제외)와 같은지, 모든 파일이 validate\_predictions를 통과하고 horizon\_step이 1..pred\_len인지 검사하고 skipped 사유별 개수를 출력한다. 성과 수치는 읽지 않는다.
6. **RunPod/** (운영 스크립트, RunPod/README.md가 절차서):
   - `setup_runpod.sh` (Pod): /workspace/venv에 Python 3.12 환경을 만들고 requirements-infer.txt를 설치한다(이미지의 torch는 쓰지 않는다). Kronos 코드 저장소를 `kronos_repo_commit`으로 clone·checkout하고, 모델·토크나이저를 revision으로 미리 받는다. 핀 값이 null이면 중단한다. 설치된 torch 버전이 핀과 다르거나 CUDA를 못 쓰면 중단한다. `nvidia-smi`와 `collect_env`를 출력한다.
   - `runpod.sh` (Pod 진입점): 환경변수 읽기(셸 → PID 1) → 수정된 추적 파일이 있으면 거부 → `results/<run_id>` 브랜치 → push 권한 사전 검사 → setup → cloud\_run.json 기록 → 입력 대조(`pod_inputs verify`) → GPU 스모크 → run\_inference → `verify_run` → `checksum write` → 메타데이터 push. 어떤 종료 경로에서도 상태를 기록하고 push한다. Pod를 종료하지 않는다.
   - `push_meta.sh` (Pod): `results/*` 브랜치에서만 동작하고 메타데이터 네 파일만 커밋·push한다.
   - `local.sh` (로컬): `push-code`, `upload <host> <port>`(입력 전송), `fetch <run_id>`(rsync + `checksum verify`), `same <a> <b>`(두 실행의 sha256 비교), `merge`/`drop`, `terminate`(verify 통과 시에만), `ssh`, `list`, `status`.
7. **CPU 스모크 테스트**: KronosBackend를 device=cpu로, 리밸런싱일 1개 × 종목 5개 × sample\_count 2로 run\_id `smoke_cpu`에 생성한다. validate\_predictions를 통과하고 manifest에 환경 정보가 있어야 한다. 이 결과 폴더는 커밋하지 않는다.

**테스트** (tests/test\_pod\_tools.py)

- pod\_inputs의 목록에 프로필 유니버스 변형과 prices만 있고 data/krx\_raw가 없다
- 입력 파일 하나를 1바이트 바꾸거나 빼면 pod\_inputs verify가 실패한다
- verify\_run이 파일 수 불일치와 horizon 불일치를 잡는다
- push\_meta.sh가 main에서는 거부하고, 결과 브랜치에는 메타데이터만 올린다. local.sh merge는 로컬 verify 통과 후에만 main에 병합한다 (로컬 bare 저장소로 검증)
- checksum write 후 verify가 통과하고, 파일 하나를 지우거나 바꾸면 실패한다
- collect\_env가 CPU에서 필수 키를 모두 돌려준다 (GPU 항목은 None)
- (선택 2를 고른 경우) aggregate가 sample\_id 앞에서부터 n\_samples개만 쓴다

### C-2. Pod 실행 (사용자 작업, 명령은 RunPod/README.md)

1. **Pod 생성**: Secure Cloud, RTX 4090 1장, On-Demand. 네트워크 볼륨(50GB)을 /workspace에 연결한다. 볼륨은 특정 데이터센터에 묶이므로 4090 재고가 있는 곳에 만든다. TCP 22를 expose하고 환경변수 `GITHUB_TOKEN`, `PUBLIC_KEY`를 넣는다. 이미지는 RunPod 공식 이미지면 된다(RunPod/README.md §5). 실행 시점의 시간당 요금을 기록한다.
2. **코드와 입력**: 로컬에서 `local.sh push-code` 후 Pod에서 레포를 /workspace에 clone한다. 로컬에서 `local.sh upload <host> <port>`로 입력 데이터를 보낸다.
3. **하루치 probe**: 첫 리밸런싱일 하나로 `RUN_ID=probe_<날짜> INFER_ARGS="--start <날짜> --end <날짜> --batch-size N" bash RunPod/runpod.sh`를 실행한다. 본 run\_id와 섞지 않는다.
   - 날짜당 소요 시간(manifest `elapsed_by_date`)과 GPU 메모리 최대치를 기록한다.
   - batch\_size를 키워 처리량이 더 늘지 않는 지점을 골라 설정에 기입하고 push-code한다.
   - 같은 날짜를 다른 run\_id(`probe_<날짜>_b`)로 한 번 더 돌리고, 둘을 fetch한 뒤 `local.sh same`으로 비트 단위로 같은지 기록한다 (결정성).
   - verify\_run이 통과하는지 확인한다(runpod.sh가 실행한다).
   - 분할 이력이 있는 종목의 예측 가격이 as\_of 원주가 수준인지 확인한다 (Stage 6의 \[확인 필요\] 항목과 같다).
   - 예상 총시간 = 날짜당 시간 × 리밸런싱일 수, 예상 비용 = 예상 총시간 × 시간당 요금.
4. **전체 실행**: tmux 세션 안에서 `RUN_ID=kronos_base_v1 bash RunPod/runpod.sh`를 실행한다. 출력은 /workspace 볼륨의 레포 아래에 쓰인다. SSH 연결이 끊겨도 프로세스는 유지된다.
5. **Pod에서 검증**: runpod.sh가 추론 뒤에 `verify_run`(as\_of 파일 수 = rebalance\_dates 수, validate\_predictions 전부 통과, skipped 사유별 개수)과 `checksum write`를 실행하고 메타데이터를 결과 브랜치로 push한다.
6. **회수**: 로컬에서 `local.sh fetch kronos_base_v1`(rsync 후 `checksum verify`)을 통과시키고 `local.sh merge kronos_base_v1`로 메타데이터를 main에 병합한다.
7. **반납**: verify 통과 후 `local.sh terminate kronos_base_v1`로 Pod를 종료한다. 네트워크 볼륨은 로컬 백업을 하나 더 만든 뒤 웹에서 삭제한다. 남겨 두면 월 단위로 계속 과금된다.

**중단·실패 대응**

| 상황 | 대응 |
| --- | --- |
| Pod 중단 | 같은 볼륨으로 새 4090 Pod를 띄워 같은 `RUN_ID`로 runpod.sh를 재실행한다. 이미 있는 날짜 파일은 건너뛰고, 재개된 날짜는 env\_by\_date로 구분된다 |
| SSH 연결 끊김 | tmux 세션에 다시 붙는다 |
| GPU 메모리 부족 | batch\_size를 절반으로 줄인다 |
| 날짜 파일 손상 (verify\_run 실패) | checksum write 전이면 해당 파일만 지우고 재실행한다. write 후라면 새 run\_id로 처음부터 생성한다 |
| 4090이 아닌 GPU로 이어서 돌려야 함 | 같은 run\_id에 섞지 않는다. 새 run\_id로 처음부터 생성한다 |

**산출물**

| 파일 | 커밋 | 내용 |
| --- | --- | --- |
| data/B\_predictions/kronos\_base\_v1/as\_of=YYYY-MM-DD.parquet | 안 함 | 원시 예측 샘플 |
| data/B\_predictions/kronos\_base\_v1/manifest.json | 함 (결과 브랜치 → merge) | 모델·추론 설정, revision, 코드 커밋, env, env\_by\_date, elapsed\_by\_date, skipped\_by\_date |
| data/B\_predictions/kronos\_base\_v1/checksums.json | 함 | 파일별 바이트와 sha256 |
| data/B\_predictions/kronos\_base\_v1/cloud\_run.json, requirements.lock.txt | 함 | 코드 커밋, pod·GPU, 세션, 상태 / 실제 설치된 패키지 |
| RunPod/inputs.sha256.json | 함 | 입력 데이터의 바이트와 sha256 |
| docs/stage\_reports/pod\_run.md | 함 | 사용자가 작성하는 실행 기록: Pod 종류와 클라우드 등급, 시간당 요금, 총 시간, 청구액, 중단 여부, probe 결과 |

**완료 기준**

- [ ] C-1 테스트와 CPU 스모크 테스트 통과
- [ ] probe 결과(날짜당 시간, batch\_size, 결정성, 가격 스케일)가 pod\_run.md에 있다
- [ ] as\_of 파일 수가 rebalance\_dates 수와 같고, validate\_predictions가 전부 통과한다
- [ ] 로컬 checksum verify 통과 후 Pod를 반납했다

**사용자 확인**: probe의 날짜당 시간과 예상 비용, 결정성 결과, 날짜별 skipped 종목 수, 분할 종목의 예측 가격 스케일, 실제 청구액

## 부록 D. 원 논문 투자 시뮬레이션 설정과 한국 적용

출처: Kronos 논문(arXiv 2508.02739) §4.2.3 "Investment Simulation", 부록 D.3.3, 표 6 "Inference hyperparameters for downstream tasks"; 공식 레포 `finetune/config.py`, `finetune/qlib_test.py` (2026-10-03 확인). 논문 본문과 레포가 다른 항목은 둘 다 적고 어느 쪽을 쓰는지 표시했다.

| 항목 | 논문 / 레포 | 이 프로젝트의 paper 프로필·TopK | 차이와 이유 |
| --- | --- | --- | --- |
| 시장·유니버스 | 중국 A주. CSI 300(대형주) 구성종목, CSI 800(중형 포함) 구성종목 | 결정 사항: (a) KOSPI 시총 상위 200 point-in-time ≈ CSI 300, (b) KOSPI+KOSDAQ 풀링 base ≈ CSI 800 | 한국에 같은 지수 구성종목 자료가 없어 시총 순위(point-in-time)로 근사 |
| 기간 | 테스트는 사전학습 종료(2024-06) 이후. 레포 backtest\_time\_range 2024-07-01 \~ 2025-06-05 | 2024-07-01 \~ 2025-06-30 | 같은 out-of-sample 논리 |
| 입력 | 일봉, lookback 90 | lookback 90 | 동일 |
| 예측 길이 | H = 10 | pred\_len 10 | 동일 |
| 샘플링 | T 0.6, top-p 0.90, N 10 (표 6). 레포 config는 inference\_sample\_count 5 | T 0.6, top\_p 0.9, sample\_count 10 | 논문 표 6을 따른다 |
| 시그널 | R\_{t→t+H} = (1/H Σ\_{i=1..H} p̂\_{t+i} − p\_t) / p\_t, 종가 예측 사용. 레포는 'mean' 시그널 = 스텝 평균 − 마지막 종가 (샘플은 predictor가 평균) | exp\_ret\_mean = 샘플·스텝 평균 pred\_close / last\_close − 1 | 동일. 원시 샘플을 저장하므로 샘플 평균을 C에서 한다 |
| 순위·선택 | 매일 순위. Top-k 동일가중 | 매일 순위(schedule daily), 신규 매수 1/k | 동일 |
| k, n | CSI 300: k 50, n 5. CSI 800: k 200, n 10. 레포 n\_symbol\_hold 50, n\_symbol\_drop 5 | 결정 사항. (a)면 k 50·n 5, (b)면 k 200·n 10 | 유니버스 크기 비율을 맞춘다 |
| 최소 보유 | 5일 (레포 hold\_thresh 5) | hold\_min\_days 5 | 동일 |
| 교체 규칙 | Qlib TopkDropoutStrategy: 보유 + 미보유 상위 n 합집합의 하위 n개 매도(bottom), 미보유 상위 매수(top) | Stage 6 작업 1의 규칙 | 동일. 보유 종목 비중은 보류(NaN)로 표현 |
| 매수 금액 | Qlib: 매도 대금 + 현금을 매수 종목 수로 나눠 전부 배분 | 신규 매수 1/k 고정, 가용 비중 부족 시 비례 축소 | **차이**. drift로 남는 현금이 조금 생길 수 있다. 비중 파일 규약을 단순하게 유지하기 위함 |
| 체결 | 다음날 시가 (레포 deal\_price open) | 다음 거래일 시가 | 동일 |
| 비용 | 본문 "거래당 0.15%". 레포 qlib open\_cost 0.1%, close\_cost 0.15%, min\_cost 5 | 기본 kr(수수료 0.015% + 슬리피지 5bp + 거래세 0.18/0.15%). `--costs paper`로 매수 0.10%·매도 0.15% 시나리오도 돌린다 | 한국 실제 비용이 주 결과, 논문 비용은 민감도 |
| 가격제한 | 레포 limit\_threshold 0.095 (A주 ±10%) | v2 price\_limit\_pct (한국 ±30%) | 시장 차이. v1에는 없음 |
| 벤치마크 | CSI 300 지수 (레포 SH000300) | 결정 사항: 코스피 지수 / 코스닥 지수 / 유니버스 EqualWeight | — |
| 지표 | AER(연율화 초과수익), IR. 레포는 excess\_return\_with/without\_cost | AER, IR을 portfolio\_metrics에 같은 이름으로 넣고 비용 전후 둘 다 | 동일 |
| 모델 | Kronos-base(및 small, mini) zero-shot | Kronos-base + Kronos-Tokenizer-base zero-shot | 동일 |

**해석 시 유의**: 논문 결과(그림 4(e), 그림 9)는 누적수익 곡선과 AER·IR 비교이며 본문에 수치 표가 없다. 재현의 목적은 수치 일치가 아니라 "같은 설정을 다른 시장에 적용했을 때의 상대 성과(벤치마크 대비, naive 대조군 대비)"를 보는 것이다. 유니버스 근사(시총 순위)와 매수 금액 규칙 차이는 결과에 영향을 줄 수 있으므로 보고서에 항상 함께 적는다.

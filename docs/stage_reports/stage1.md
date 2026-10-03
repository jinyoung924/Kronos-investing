# Stage 1 보고서

2026-10-03. 범위: docs/decisions.md D-1·D-2·D-4·D-6·D-7·D-8·D-9 반영 + docs/spec.md "Stage 1. A_data_prepare — 기준 데이터". 다음 Stage는 시작하지 않았다.

## 구현한 것 (파일 목록과 한 줄 설명)

**결정 기록 반영 (configs/base.yaml, 주석에 결정 번호)**

| 결정 | 반영 |
| --- | --- |
| D-1 | `data.price_basis: raw` |
| D-2 | `data.markets` 주석에 "새 코드가 읽는 유일한 market 키" 명시. Stage 1 코드는 `data.markets`만 읽는다(유니버스의 market은 가격 행에서 조인, index→market 매핑 키를 읽지 않음). `krx.markets`·`universe.markets`는 수집·load_prices 전용으로 그대로 |
| D-4 | `universe.variant: base` 유지, 주석 |
| D-6 | `data.index_tickers` 주석. Stage 1이 지수(KOSPI, KOSDAQ)와 ETF(226490, 229200)를 모두 `data/A_prepared/benchmark.parquet`에 노출(`kind` 컬럼 index / etf) |
| D-7 | `model:`·`infer:`·`project.seed` 주석. 구조 변경 없음 |
| D-8 | requirements.txt 머리말에 D-8 표기, 버전 변경 없음, torch 미포함 |
| D-9 | run_prepare도 기존 로거 이름 `data_prepare`(logs/data_prepare.log)를 쓴다 |

**A_data_prepare (Stage 1)**

| 파일 | 설명 |
| --- | --- |
| build_prices.py | `build_prices(raw_by_index, markets)`: 두 거래소 합치고 exchange→market, trdval→value, list_shrs→listed_shares로 통일, `common.schema.validate_prices` 통과. `listing_span` |
| build_calendar.py | `build_calendar(prices)`(date), `halt_flag`, `build_halts(prices, raw_halted)`(date, ticker, is_halted; 수집 `halted`와 불일치하면 오류) |
| build_adj_factor.py | `build_events(raw_events)`(ticker, ex_date, ratio, r=1/ratio, applied, source, name, prev_close, base_price), `build_adj_factor(prices, events)`(date, ticker, factor; 첫 행 1, 적용 ex-date에만 r 곱), `adjusted_close`, `unexplained_moves(prices, adj, halts, max_move, delist_window)` |
| build_universe.py | `build_universe(constituents_by_index, prices, top_n_mktcap)`(date, ticker, market; market은 같은 날 가격 행에서, top_n은 close×listed_shares 상위 N), `members_per_date` |
| run_prepare.py | 진입점. `python -m A_data_prepare.run_prepare [--variant] [--top-n-mktcap]`. 파일 읽기·쓰기는 여기서만. 산출물 7개 parquet + unexplained_moves.csv + universe_members_by_date.csv + meta.json(입력 파일 해시 7개) |
| __init__.py | docstring 갱신 |

**common**

| 파일 | 설명 |
| --- | --- |
| common/calendar.py (신규) | `as_calendar`, `is_trading_day`, `next_trading_day(cal, d)`, `prev_trading_day`, `shift_trading_days(cal, d, n)`, `rebalance_dates(cal, start, end, step)` |
| common/data.py | `rebalance_dates`를 common.calendar에서 재수출(같은 객체). run_inference 호출부 불변 |
| common/universe.py | `get_universe(universe, date, market=None)` 추가 (= universe_at + market 필터) |

**probe·테스트·문서**

| 파일 | 설명 |
| --- | --- |
| scripts/probes/stage1_halts.py | [확인 필요] 거래정지 표현 probe (캐시 JSON 직접 열람, 네트워크 없음) |
| scripts/probes/stage1_figures.py | 사용자 확인용 그림 2장 → docs/stage_reports/figures/ |
| tests/test_stage1_data.py | 10개 (아래) |
| README.md | A_prepared 산출물과 실행 명령 추가 |

## 실행한 명령과 결과 (테스트 통과 수, 주요 수치)

```text
pytest                                    43 passed (Stage 0까지 33 + test_stage1_data.py 10)
python -m A_data_prepare.run_prepare      6.0초. data/A_prepared/
  prices      1,666,220행, 2,628종목, 2022-10-04 ~ 2025-07-15, market {KOSPI, KOSDAQ}
  calendar    680 거래일
  halts       1,666,220행, is_halted 55,114행 (3.31%)
  adj_factor  1,666,220행, 첫 행 F = 1.0
  events      680건 (전부 applied), ratio 0.0131 ~ 50.0, r = 1/ratio 0.02 ~ 76.1
  universe    251,098행, 271 스냅샷(2024-06-03 ~ 2025-07-15), 일별 846 ~ 1,101종목 (KOSPI 339 ~ 468, KOSDAQ 480 ~ 635)
  benchmark   2,720행 (KOSPI, KOSDAQ 지수 + ETF 226490, 229200, 각 680일)
  unexplained_moves.csv  58행 전부 liquidation, unexplained 0
  meta.json   stage A_data_prepare, config_hash ee5a1a4a2a19, 입력 7개 sha256
python scripts/probes/stage1_halts.py     아래 "[확인 필요]" 절
python scripts/probes/stage1_figures.py   docs/stage_reports/figures/stage1_*.png
```

## 완료 기준 체크 (명세서 항목별 통과/실패)

- [x] 테스트 통과 (tests/test_stage1_data.py 10개, 전체 43개)
  - 분할 샘플(ratio ≤ 0.2인 5:1 이상 분할 전부)과 적용 이벤트 680건 전부: ex-date 수정 종가 수익률 |r| ≤ 30%, 같은 날 원주가 수익률은 한도 밖
  - 전 종목 |수정 수익률| > 35% 목록 생성, 전부 liquidation으로 분류됨
  - cut 이후 이벤트 삭제·가짜 분할 추가·가격 절반 변경 → cut 이전 F 불변
  - get_universe(d) = d 이하 최신 스냅샷(= universe_at), 상장폐지 종목이 마지막 구성일까지 남고 소급 제거 없음, market = 그날 가격 행의 market
  - (date, ticker) 중복 없음, 가격 음수 없음 (validate_prices), 정지일 open/high/low NaN·거래일 전부 유효
  - 추가: F_t/F_s == 기존 adj_factor_t/adj_factor_s (전 종목 연속 행, rtol 1e-9), F 변화일 집합 == 적용 ex-date 집합, top_n_mktcap=200이 날짜별 정확히 200개·시총 상위와 일치, 달력 함수와 rebalance_dates(B와 같은 함수, 49개 날짜), 디스크 산출물 == 메모리 빌드, 벤치마크 캘린더 == 주식 캘린더
- [x] "설명되지 않은 급변" 목록이 보고서에 있고 각 건에 판단이 있다 (아래)

### 설명되지 않은 급변 목록 (|수정 단순수익률| > 35%, 신규 상장일·정지 해제일 제외)

58행 · 30종목. 전부 상장폐지 전 정리매매 구간(마지막 가격 행까지 14거래일 이내)이며, 누락 이벤트로 판단한 건은 없다.

| 종목 | 행 | 날짜 (수익률) | 마지막 가격일 | 판단 |
| --- | --- | --- | --- | --- |
| 015540 쎌마테라퓨틱스 (KOSPI) | 2 | 10-30 -37%, 11-01 +43% | 2023-11-06 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 023460 CNH (KOSDAQ) | 2 | 07-10 -41%, 07-11 -44% | 2025-07-11 | 정리매매 (마지막 행까지 ≤ 1거래일) |
| 030790 비케이탑스 (KOSPI) | 1 | 05-10 -58% | 2024-05-10 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 038340 UCI (KOSDAQ) | 3 | 03-21 +53%, 03-24 -40%, 03-27 -68% | 2025-03-27 | 정리매매 (마지막 행까지 ≤ 4거래일) |
| 050540 한국코퍼레이션 (KOSDAQ) | 2 | 04-28 -63%, 05-02 -37% | 2023-05-04 | 정리매매 (마지막 행까지 ≤ 3거래일) |
| 053590 한국테크놀로지 (KOSDAQ) | 2 | 10-02 -50%, 10-04 -50% | 2024-10-04 | 정리매매 (마지막 행까지 ≤ 1거래일) |
| 056000 코원플레이 (KOSDAQ) | 1 | 03-29 -52% | 2023-03-29 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 058420 제이웨이 (KOSDAQ) | 1 | 04-13 -60% | 2023-04-13 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 065560 녹원씨엔아이 (KOSDAQ) | 1 | 07-23 +44% | 2024-07-26 | 정리매매 (마지막 행까지 ≤ 3거래일) |
| 071460 위니아 (KOSDAQ) | 1 | 06-17 -57% | 2025-06-17 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 072520 제넨바이오 (KOSDAQ) | 4 | 06-11 +195%, 06-12 -40%, 06-16 -41%, 06-17 -62% | 2025-06-17 | 정리매매 (마지막 행까지 ≤ 4거래일) |
| 078650 지나인제약 (KOSDAQ) | 4 | 07-17 -51%, 07-18 -53%, 07-19 -39%, 07-20 -69% | 2023-07-20 | 정리매매 (마지막 행까지 ≤ 3거래일) |
| 078940 코드네이처 (KOSDAQ) | 2 | 02-11 -40%, 02-12 +52% | 2025-02-18 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 090740 연이비앤티 (KOSDAQ) | 1 | 10-13 -46% | 2022-10-14 | 정리매매 (마지막 행까지 ≤ 1거래일) |
| 096640 멜파스 (KOSDAQ) | 1 | 07-14 -44% | 2023-07-14 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 114120 크루셜텍 (KOSDAQ) | 1 | 02-14 -64% | 2024-02-14 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 136510 스마트솔루션즈 (KOSDAQ) | 1 | 07-18 -46% | 2024-07-24 | 정리매매 (마지막 행까지 ≤ 4거래일) |
| 141020 디에스앤엘 (KOSDAQ) | 3 | 12-21 -41%, 12-22 -35%, 01-02 -58% | 2024-01-02 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 148140 비디아이 (KOSDAQ) | 3 | 05-31 -47%, 06-03 -41%, 06-04 -37% | 2024-06-05 | 정리매매 (마지막 행까지 ≤ 3거래일) |
| 158310 참존글로벌 (KOSDAQ) | 2 | 11-02 -36%, 11-08 +37% | 2022-11-08 | 정리매매 (마지막 행까지 ≤ 4거래일) |
| 160600 이큐셀 (KOSDAQ) | 1 | 02-11 +39% | 2025-02-13 | 정리매매 (마지막 행까지 ≤ 2거래일) |
| 181340 이즈미디어 (KOSDAQ) | 1 | 07-02 -35% | 2024-07-09 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 182690 테라셈 (KOSDAQ) | 4 | 10-07 -40%, 10-12 -37%, 10-13 -56%, 10-14 -50% | 2022-10-14 | 정리매매 (마지막 행까지 ≤ 4거래일) |
| 214310 에스엘바이오닉스 (KOSDAQ) | 2 | 12-13 -39%, 12-20 -36% | 2024-12-20 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 226440 한송네오텍 (KOSDAQ) | 1 | 06-18 -59% | 2025-06-18 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 263540 어스앤에어로스페이스 (KOSDAQ) | 1 | 11-05 -43% | 2024-11-05 | 정리매매 (마지막 행까지 ≤ 0거래일) |
| 268600 셀리버리 (KOSDAQ) | 2 | 02-26 -41%, 03-05 -42% | 2025-03-06 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 299910 베스파 (KOSDAQ) | 2 | 02-06 -42%, 02-10 -43% | 2025-02-13 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 323230 엠에프엠코리아 (KOSDAQ) | 4 | 06-11 +68%, 06-16 -35%, 06-17 -45%, 06-18 -50% | 2025-06-18 | 정리매매 (마지막 행까지 ≤ 5거래일) |
| 900280 골든센츄리 (KOSDAQ) | 2 | 01-06 -40%, 01-07 -67% | 2025-01-07 | 정리매매 (마지막 행까지 ≤ 1거래일) |

- 수집 파이프라인의 residual_big_moves(96행)보다 적은 이유: 명세 규칙대로 **정지 해제일**(직전 행이 정지)을 뺐기 때문이다. 제외된 행은 38 rows (38 tickers), ret range -98% .. +197%이며, 전부 장기 정지 뒤 정리매매 첫날의 급락(또는 정지 중 기준가 대비 재개 첫날 급변)이다. 데이터 오류나 누락 이벤트는 아니다.
- 결론: 조정계수가 빠뜨린 기업행위는 없다. 정리매매 가격은 그대로 남아 있으므로(생존편향 없음) `backtest.delist_policy`가 이 손실의 처리 방식을 정한다(Stage 4 사용자 결정).

## [확인 필요] 항목의 probe 결과

scripts/probes/stage1_halts.py (data/krx_raw/2026-09-28 캐시 JSON 직접 열람):

| 확인 | KOSPI | KOSDAQ |
| --- | --- | --- |
| `halted` 행 | 11,851 / 569,720 (2.08%) | 43,263 / 1,096,500 (3.95%) |
| 정지 표본 15행의 원본 표현 | 15/15: OHL "0", 거래량 "0", 종가 > 0, 전일대비 "0", 등락률 "0.00" | 동일 15/15 |
| 거래량 > 0인데 시가 0 | 1행 (2025-03-21 145210 다이나믹디자인, 거래량 1,015주) | 1행 (2023-04-24 089530 에이티세미콘, 120주) |
| 시가 > 0인데 거래량 0 | 0 | 0 |
| NaN 종가 | 0 | 0 |
| 상장 기간 안에서 응답에서 빠진 날 | 0종목 0일 | 0종목 0일 |
| 마지막 행이 달력 끝 이전 (폐지·이전상장) | 15 | 65 |

- 규칙 확인: 정지일은 응답에 남고 OHL 0·거래량 0, 종가(기준가)만 유효하다. 수집 파일의 `halted = (volume == 0) | (open <= 0)`과 open/high/low NaN 처리가 그 규칙과 일치한다. Stage 1의 halts는 prepared prices에서 같은 규칙으로 다시 계산해 수집 `halted`와 전 행 일치함을 확인한 뒤 저장한다.
- 응답에서 아예 빠지는 경우는 상장폐지(그리고 이전상장 시 한쪽 파일에서 사라지고 다른 쪽에 나타나는 7종목)뿐이다. 두 거래소를 합친 prepared prices에서는 이전상장 종목도 빈 날이 없다.
- 예외 2행(거래량 1,015주·120주, 시가 0): 시가 없이 소량 체결된 날이다. 현재 규칙은 "시가가 없다"를 우선해 정지로 분류한다. 체결일에 시가가 없으면 엔진이 거래할 수 없으므로 이 분류가 맞다.
- 전 기간(680일) 정지인 종목이 있다(선도전기 007610, 피에이치씨 057880). 가격 표에는 있고 유니버스에는 들어오지 않는다.

## 명세서와 달라진 점과 이유

1. **halts 표는 모든 (date, ticker) 행을 담는다** (정지 행만이 아님). "행 없음 = 미상장"과 "is_halted False = 거래"를 구분하기 위해서다. 1.67M행이지만 parquet 258 KB.
2. **events 컬럼**: 명세의 (ticker, ex_date, ratio, source)에 `r`(= 1/ratio, F에 곱한 값), `applied`, `name`, `prev_close`, `base_price`를 더했다. ratio는 수집 파이프라인 정의(조정 후 / 조정 전, 2:1 분할 = 0.5) 그대로 두고 방향 혼동을 막기 위해 r을 따로 둔다. source는 `krx_reference_price` 하나다.
3. **universe에 market 컬럼을 가격 행에서 조인한다**(D-2). 명세 표의 "두 거래소를 합친다(market 컬럼)"를 index→market 매핑 없이 구현했다. 유니버스 행에 그날 가격이 없으면 오류(sanity 게이트 4가 보장, 실제 0건).
4. **benchmark.parquet 컬럼**: date, ticker, kind(index/etf), name, open, high, low, close, volume. 수집 파일의 `market`(값 "KR", 옛 비용 시장 개념)과 `adj_factor`(전부 1)는 뺐다. 지수·ETF는 참고선이지 거래 대상이 아니므로 market이 필요 없다(D-6).
5. **진단 임계값을 설정 키로 두었다**: `data.max_daily_move: 0.35`, `data.delist_window_days: 14` (공통 규칙 9: 숫자를 코드에 두지 않음). 가격제한폭 자체(`backtest.v2.price_limit_pct`)는 사용자 값이라 그대로 null이고, 테스트의 30%는 테스트 상수다.
6. **top_n_mktcap**: 명세 Stage 6 요구에 맞춰 `build_universe`에 구현했다(close × listed_shares, 날짜별 상위 N, 풀링 유니버스 기준). "코스피 상위 200"을 원하면 `universe.indices: [kospi]`와 함께 쓴다. 기본 null이라 이번 산출물에는 영향 없다.
7. **common/calendar.rebalance_dates가 공식 구현**이고 common.data.rebalance_dates는 같은 객체를 재수출한다(중복 제거, B_model_infer 호출부 그대로).
8. **정지 해제일 제외 규칙의 부작용**: 정리매매 첫날 급락 38행이 목록에서 빠진다(위 설명). 목록의 목적(누락 이벤트 탐지)에는 영향이 없다.
9. **A_data_prepare/run.py 등 수집 코드는 건드리지 않았다.** Stage 1은 data/raw·data/universe만 읽는다. data/ 아래 수집 경로에는 쓰지 않았다(data/A_prepared/만 생성).
10. 명세의 테스트 파일명 tests/test_stage1_data.py를 그대로 썼다. 실제 수집 데이터가 없으면 skip된다(Windows에서는 `manifest --verify` 후 실행).

## 사용자 확인 요청 (사용자가 직접 볼 파일·차트·수치)

1. **분할 종목 원주가·수정주가 그래프**: [figures/stage1_splits_raw_vs_adjusted.png](figures/stage1_splits_raw_vs_adjusted.png) — BYC(001460, 2024-04-17 10:1), 남양유업(003920, 2024-11-20 10:1), 영풍(000670, 2025-04-25 10:1). 주황(수정주가)이 ex-date에서 끊기지 않고, 파랑(원주가)만 1/10로 떨어지는지. 영풍은 2025-04 정지 구간이 평평하게 보인다(정지 중 기준가 유지).
2. **날짜별 유니버스 종목 수 그래프**: [figures/stage1_universe_members.png](figures/stage1_universe_members.png) — 전체 846 ~ 1,101, KOSPI 339 ~ 468, KOSDAQ 480 ~ 635. 회색 띠가 평가 기간. 2025-06 이후 증가는 2023-10 이후 상장 종목들이 400봉 이력을 채우기 시작한 효과로 보인다(확인 요청).
3. **거래정지 probe 결과**(위 표)와 예외 2행의 분류(정지로 처리)가 맞는지.
4. **설명되지 않은 급변 목록**(위 표): 전부 정리매매로 판단한 것이 맞는지.
5. data/A_prepared/meta.json의 입력 해시가 MANIFEST.json과 같은 파일을 가리키는지(선택).

## 질문과 미결정 사항

1. **D-3·D-5는 구현하지 않았다**(지시대로). Stage 2에서 D-3의 "자격 판정 함수 분리"를 할 때 build_batch의 `_window`를 공용 함수로 뽑을 예정이다.
2. **벤치마크 market**: 지수·ETF에 market을 두지 않았다. Stage 5에서 ETF를 거래 가능한 참고선으로 비용까지 붙여 보려면 그때 market(ETF는 KOSPI 상장)을 추가한다. 지금은 필요 없다고 판단했다.
3. **정지 해제일 제외 규칙**: 정리매매 첫날 급락이 목록에서 빠지는 것이 의도와 맞는지. 포함하려면 `unexplained_moves`의 `prev_halted` 제외를 끄면 된다(모두 liquidation으로 분류됨).
4. **docs/outline.md의 로컬 수정분**은 이번 커밋에도 넣지 않았다(사용자 문서). 커밋하려면 알려 주세요. docs/ 아래 11 MB PDF도 그대로 untracked다.
5. 아직 null인 사용자 값(docs/decisions.md "아직 정하지 않은 것")은 Stage 1에서 필요하지 않았다.

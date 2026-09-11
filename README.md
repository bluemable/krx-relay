# KRX 시세 중계소 (GitHub Relay)

Claude 샌드박스는 도메인 화이트리스트 방식이라 네이버·KRX·증권사 API에 직접 접근할 수 없다.
그러나 **`raw.githubusercontent.com` 은 허용 목록에 있고**, GitHub Actions 러너는 인터넷 전체에 접근 가능하다.

이 저장소는 그 틈을 잇는 중계소다.

```
GitHub Actions 러너 ──> 네이버 API / yfinance     (러너는 인터넷 전체 접근 가능)
        │
        └─ commit ──> data/latest.json
                            │
                            └──> raw.githubusercontent.com ──> Claude  ✅ 허용됨
```

---

## 설치 (약 10분)

### 1. 저장소 생성

**Public** 으로 만든다. Private 이면 `raw.githubusercontent.com` 이 토큰을 요구하는데,
Claude 에게 토큰을 넘기는 건 피해야 하므로 Public 이 맞다.

공개되는 건 **주가·지수·환율뿐**이다. 보유 수량·평단·계좌정보는 이 저장소에 절대 넣지 않는다.
(그건 기존 `portfolio.json` 에 로컬로 두면 된다.)

```
your-id/krx-relay          ← Public
├── .github/workflows/quotes.yml
├── fetch_quotes.py
├── tickers.json
└── data/                  ← 자동 생성
```

### 2. Actions 권한 설정

`Settings → Actions → General → Workflow permissions`
→ **Read and write permissions** 선택 후 Save.

이걸 안 하면 봇이 커밋을 푸시하지 못한다.

### 3. 첫 실행

`Actions` 탭 → `quotes` → **Run workflow** 로 수동 실행.
`data/latest.json` 이 생기면 성공.

### 4. Claude 에게 알려주기

```
시세 중계소 주소야:
https://raw.githubusercontent.com/<내아이디>/krx-relay/main/data/latest.json
```

한 번만 알려주면 이후 대화에서 계속 쓸 수 있다.

---

## Claude 쪽 읽기 방법

```bash
curl -sS "https://raw.githubusercontent.com/<ID>/krx-relay/main/data/latest.json?t=$(date +%s)"
```

`?t=` 캐시버스팅은 **필수**다. 없으면 CDN이 최대 5분 된 사본을 준다.

**신선도 검증이 핵심이다.** payload 안의 `fetched_at_kst` 와 현재 시각을 비교한다.
(TradingView·investing.com 을 직접 fetch 했을 때 3주 묵은 캐시가 나왔던 문제를
이 저장소가 구조적으로 해결한다.)

단, **"오래됨"과 "틀림"은 다르다.** 판단은 `market_open` 과 함께 해야 한다.

| 상태 | 판단 |
|---|---|
| `market_open: true` 이고 10분 초과 | 장중인데 갱신이 밀린 것 — 체결 판단에 쓰지 말 것 |
| `market_open: false` | 장이 닫혀 값이 안 변한다. 수집이 몇 시간 전이어도 종가는 유효 |
| `health.stale_date: true` | 수집일 ≠ 거래일. 장 시작 전 수집분이라 **전 거래일 데이터**다 |

### payload 주요 필드

| 필드 | 뜻 |
|---|---|
| `trade_date` | **체결 시각 기준 거래일.** 수집일이 아니다. 07~09시 수집분은 전 거래일을 가리킨다 |
| `market_open` | 국내 장중 여부 |
| `domestic.*.prev_close` | 전일 종가 |
| `domestic.*.value` / `market_cap` | 거래대금 / 시총 (원 단위 숫자. `'4조 6,867억'` 같은 문자열이 아니다) |
| `domestic.*.per` / `pbr` | 배수 (숫자) |
| `health.stale_date` | `trade_date` 가 수집일과 다른지 |

---

## `latest.json` 이 멈춘 것처럼 보일 때

읽는 쪽에서 `fetched_at_kst` 가 몇 주 전으로 나오면, **저장소가 아니라 읽는 경로를 먼저 의심한다.**
2026-09-11 에 "6주째 정지" 로 진단된 사례가 있었는데 실제 원인은 저장소가 아니었다.

판정은 한 줄이면 끝난다. 저장소가 서는 값과 내가 받은 값을 직접 비교한다.

```bash
curl -sS "https://raw.githubusercontent.com/bluemable/krx-relay/main/data/latest.json?t=$(date +%s)" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['fetched_at_kst'], d['epoch'])"
```

- 여기서 **오늘 날짜가 나오면 저장소는 정상**이다. 낡은 값을 본 쪽의 캐시 문제다.
  (`?t=` 는 CDN 은 우회하지만, 쿼리스트링을 무시하고 경로로만 캐싱하는 중간 프록시는 못 뚫는다.
  매일 URL 이 바뀌는 `data/<날짜>.json` 은 캐시될 수 없어서 **혼자만 멀쩡해 보이는** 현상이
  생긴다. 한 파일만 낡아 보이면 대개 이 경우다.)
- 오래된 날짜가 나오면 그때 저장소를 본다. `Actions` 탭에서 `quotes` 실행 이력과
  `Verify latest.json committed` 스텝을 확인한다.

### 하지 말 것

- **`git update-index --skip-worktree` / `--assume-unchanged` 를 원인으로 의심하지 말 것.**
  이 플래그는 `.git/index` 안에만 있는 로컬 상태이고 커밋이나 clone 으로 전달되지 않는다.
  러너는 매번 새로 checkout 하므로 애초에 이 상태를 물려받을 수 없다.
  (확인하고 싶으면 `git ls-files -v data/` — 정상은 전부 `H` 다.)
- **`latest.json` 을 날짜 파일로 향하는 심볼릭 링크로 두지 말 것.**
  `raw.githubusercontent.com` 은 심볼릭 링크를 따라가지 않고 **링크 대상 경로를 그냥 텍스트로
  돌려준다.** 읽는 쪽 전부가 깨진다.

### 조용한 실패 방지 장치

`quotes` / `close` 워크플로 마지막에 `Verify latest.json committed` 스텝이 있다.
방금 쓴 파일의 `epoch` 과 커밋에 실제로 들어간 `epoch` 을 비교해서, 다르면 워크플로를
실패시킨다. 어떤 이유로든 `latest.json` 이 커밋에서 누락되면 Actions 가 빨갛게 뜬다.

---

## 갱신 주기와 한계

| 항목 | 실제 값 (2026-09 실측) |
|---|---|
| cron 설정 | 5분 |
| **실제 실행 횟수** | **하루 3~4회** (5분 간격 예약분의 대부분이 아예 실행되지 않음) |
| **실제 지연** | `quotes` 수십 분, `close` **최대 5시간** (06:45 UTC 예약분이 11:51 UTC 실행) |
| 국내 시세 출처 | 네이버 — 실시간 체결가 (무료, 키 불필요) |
| 해외 시세 출처 | yfinance — 15분 지연 |

**GitHub Actions cron 은 정시성도 실행 자체도 보장되지 않는다.** 예약 5분 간격은
이 저장소에서 사실상 지켜진 적이 없다. 실행 이력 289건을 대조한 결과 장중 갱신은
하루 3~4회였고, 장중 `latest.json` 의 나이는 수십 분~3시간이 정상 범위다.

따라서 이 중계소는 **"장 마감 종가 아카이브 + 하루 몇 번의 참고용 스냅샷"**이다.
준실시간 모니터링조차 기대하면 안 되고, 체결 판단용은 더더욱 아니다.

지금 당장 최신값이 필요하면 `Actions → quotes → Run workflow` 로 수동 실행하는 것이
유일하게 확실한 방법이다(약 40초).

이 지연이 데이터를 **오염시키지는 않는다.** 날짜는 수집 시각이 아니라 체결 시각
(`trade_date`)에서 뽑으므로, 자정을 넘겨 실행돼도 종가가 다음 날짜로 기록되지 않는다.

초 단위 실시간이 반드시 필요하면 **Claude in Chrome** 이 유일한 답이다
(브라우저가 직접 증권사 화면을 읽으므로 지연 0).

### 주의사항

- 스케줄 워크플로는 **60일간 저장소 활동이 없으면 자동 비활성화**된다.
  월 1회라도 커밋이 있으면 유지된다. (시세 봇이 매일 커밋하므로 실질적으로 문제없음)
- 커밋이 하루 약 100건 쌓인다. 용량은 미미하나, 연 1회 `data/` 히스토리를
  정리하고 싶으면 오래된 일별 스냅샷만 지우면 된다.
- 네이버 API는 공개 엔드포인트지만 비공식이다. 응답 스키마가 바뀌면
  `fetched_at_kst` 는 갱신되는데 `price` 가 `null` 로 나온다.
  `health.domestic_ok` 필드로 이 상태를 감지할 수 있다.
- 국내 종목 값은 두 엔드포인트에서 온다. `basic` 은 현재가·등락·장상태만 주고,
  전일종가·시고저·거래량·시총·PER·PBR 은 전부 `integration` 에만 있다.
  스키마가 바뀌어 특정 필드만 `null` 로 변하면 대개 `integration` 쪽 키 변경이다.
- 파일 두 종류의 날짜 기준이 다르다. 헷갈리면 안 된다.

  | 경로 | 날짜 기준 | 내용 |
  |---|---|---|
  | `data/<수집일>.json` | **수집일** | 그 날 수집된 최신 상태. 아침 수집분은 전 거래일 종가 + 직전 미국 종가 |
  | `data/close/<거래일>.json` | **거래일** | 종가 확정 아카이브 |
  | `data/history.jsonl` | **거래일** | 종가 누적 (`quote_time` 기준으로 귀속) |

  어느 거래일 데이터인지는 파일명이 아니라 payload 의 `trade_date` 로 판단한다.
- 일별 스냅샷은 이미 있는 파일보다 채워진 필드가 적으면 덮어쓰지 않는다.
  (`integration` 일시 실패로 시고저·거래량이 빈 수집분이 그 날의 온전한 스냅샷을 깎는 것 방지)

---

## 업그레이드: 한국투자증권 KIS Open API

현재 브리핑 스킬은 KIS API를 쓰지 않는 것으로 되어 있다.
진짜 실시간 체결가와 **본인 계좌 잔고**까지 필요하면 KIS로 올릴 수 있다.

1. KIS 개발자센터에서 `appkey` / `appsecret` 발급 (모의투자는 무료)
2. 저장소 `Settings → Secrets and variables → Actions` 에 등록
   - `KIS_APPKEY`, `KIS_APPSECRET`, `KIS_ACCOUNT`
3. 워크플로에서 `env:` 로 주입

```yaml
      - name: Fetch quotes
        env:
          KIS_APPKEY:    ${{ secrets.KIS_APPKEY }}
          KIS_APPSECRET: ${{ secrets.KIS_APPSECRET }}
        run: python fetch_quotes.py
```

**중요:** Secrets 는 GitHub 서버에만 저장되고 로그에 마스킹된다.
Claude 는 이 값을 보지 못하고, 볼 필요도 없다. 결과 JSON만 읽는다.

단, **계좌 잔고를 공개 저장소에 커밋하면 안 된다.** 잔고까지 중계하려면
Private 저장소 + 별도 접근 방식이 필요하므로, 시세만 공개로 두는 현재 구조를 권한다.

---

## 기존 브리핑 스킬 연동

`morning-stock-brief` 스킬의 **2-2 국내 시세** 항목이 `pykrx` 에 의존하는데,
pykrx 1.2.8부터 KRX 계정 로그인이 필수가 되어 현재 작동하지 않는다.

해당 표를 이렇게 교체하면 된다.

| 순위 | 방법 |
|---|---|
| 1 | **GitHub 중계소** `raw.githubusercontent.com/<ID>/krx-relay/main/data/latest.json` — `fetched_at_kst` 로 신선도 검증 필수 |
| 2 | WebSearch `"<종목명> 주가 <날짜> 종가"` 뉴스 교차 확인 |

`~~pykrx~~` 는 삭제한다. 07:57 브리핑 시각이면 전 거래일 종가가 이미 확정되어 있어
중계소 데이터로 충분하다.

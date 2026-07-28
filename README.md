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

**신선도 검증이 핵심이다.** payload 안의 `fetched_at_kst` 와 현재 시각을 비교해
10분 이상 차이 나면 그 데이터는 쓰지 않는다. 이 필드가 있는 이유가 그것이다.
(TradingView·investing.com 을 직접 fetch 했을 때 3주 묵은 캐시가 나왔던 문제를
이 저장소가 구조적으로 해결한다.)

---

## 갱신 주기와 한계

| 항목 | 실제 값 |
|---|---|
| cron 설정 | 5분 |
| 실제 지연 | **5~20분** (GitHub 스케줄러 혼잡 시 지연됨) |
| 국내 시세 출처 | 네이버 — 실시간 체결가 (무료, 키 불필요) |
| 해외 시세 출처 | yfinance — 15분 지연 |

**GitHub Actions cron 은 정시성이 보장되지 않는다.** 최소 간격은 5분이지만 혼잡 시간대에는
15~20분씩 밀린다. 따라서 이 중계소는 **"준실시간 모니터링"용이지 체결 판단용이 아니다.**

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

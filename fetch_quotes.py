#!/usr/bin/env python3
"""
시세 중계 수집기 — GitHub Actions 러너에서 실행된다.

러너는 인터넷 전체에 접근 가능하므로 네이버/KIS를 직접 호출하고,
결과를 data/latest.json 으로 커밋한다.
Claude 는 raw.githubusercontent.com 을 통해 그 파일을 읽는다.

설계 원칙
  1. fetched_at_kst 를 반드시 payload 안에 박는다.
     -> 읽는 쪽에서 CDN 캐시로 인한 stale 데이터를 스스로 판별할 수 있어야 한다.
  2. 부분 실패해도 죽지 않는다. 실패한 항목은 errors 에 남기고 나머지를 저장한다.
  3. 숫자를 창작하지 않는다. 못 가져오면 null.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://m.stock.naver.com/"}
TIMEOUT = 15

ROOT = os.path.dirname(os.path.abspath(__file__))
errors = []


def get_json(url, tries=3, silent=False):
    """재시도 포함 GET. 실패 시 None.

    silent=True 는 '실패해도 정상'인 후보 탐색용(환율 엔드포인트 순회 등).
    이걸 구분하지 않으면 살아있는 후보로 넘어가 성공했는데도 매 실행 errors 에
    같은 404 가 쌓여, 진짜 장애와 구분이 안 된다.
    """
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if not silent:
                errors.append(f"HTTP {r.status_code}: {url}")
        except Exception as e:
            if not silent:
                errors.append(f"{type(e).__name__}: {url} :: {str(e)[:120]}")
        if i + 1 < tries:
            time.sleep(1.5 * (i + 1))
    return None


_SUFFIX = ("배", "원", "주", "%", "pt", "P")
_KR_UNIT = {"조": 10 ** 12, "억": 10 ** 8, "만": 10 ** 4}


def f(v):
    """'254,000' / '-5.31' / '11.67배' / '46.71%' -> float | None

    네이버 integration 엔드포인트는 값에 단위를 붙여서 준다('11.67배', '22,292원').
    접미사를 떼지 않으면 float() 가 실패해 그대로 null 이 된다.
    """
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    for suf in _SUFFIX:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            break
    try:
        return float(s)
    except (ValueError, AttributeError):
        return None


def f_won(v):
    """'1,521조 4,940억' / '4조 6,867억' / '46867' -> float(원) | None

    시총·거래대금은 한국식 축약 단위로 온다. 일반 float() 로는 못 읽는다.
    """
    if v is None:
        return None
    s = str(v).replace(",", "").replace(" ", "").replace("원", "").strip()
    if not s:
        return None
    total, buf, seen_unit = 0.0, "", False
    for ch in s:
        if ch.isdigit() or ch == "." or (ch == "-" and not buf):
            buf += ch
        elif ch in _KR_UNIT:
            if not buf:
                return None
            try:
                total += float(buf) * _KR_UNIT[ch]
            except ValueError:
                return None
            buf, seen_unit = "", True
        else:
            return None
    if buf:
        try:
            total += float(buf)
        except ValueError:
            return None
    elif not seen_unit:
        return None
    return total


def trade_date_of(quote_time):
    """localTradedAt('2026-09-10T16:10:20+09:00') -> '2026-09-10' | None

    수집 시각이 아니라 '체결 시각' 기준 거래일. 장 시작 전(07~09시)에 수집하면
    quote_time 은 전 거래일 16:10 을 가리키므로, 이 값을 써야 전일 종가가
    당일 종가로 둔갑하지 않는다.
    """
    if not quote_time:
        return None
    s = str(quote_time)
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else None


# ---------------------------------------------------------------- 국내 종목
def fetch_domestic(code, name):
    d = get_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    if not d:
        return {"name": name, "error": "fetch_failed"}

    quote_time = d.get("localTradedAt")
    out = {
        "name": d.get("stockName") or name,
        "price": f(d.get("closePrice")),
        "change": f(d.get("compareToPreviousClosePrice")),
        "change_pct": f(d.get("fluctuationsRatio")),
        "market_status": d.get("marketStatus"),
        "quote_time": quote_time,
        "trade_date": trade_date_of(quote_time),
        "prev_close": None, "market_cap": None, "per": None, "pbr": None,
        "open": None, "high": None, "low": None, "volume": None, "value": None,
        "high52": None, "low52": None,
    }

    # basic 엔드포인트가 주는 건 현재가·등락·상태뿐이다.
    # 전일종가·시총·PER·PBR·시고저·거래량은 전부 integration 에만 있다.
    # (basic 에 previousClose/marketValue/per/pbr 키는 존재하지 않는다 — 예전엔
    #  그걸 읽으려다 이 필드들이 항상 null 로 나갔다.)
    ext = get_json(f"https://m.stock.naver.com/api/stock/{code}/integration", tries=2)
    if ext:
        td = (ext.get("totalInfos") or [])
        kv = {i.get("code"): i.get("value") for i in td if isinstance(i, dict)}
        out["prev_close"] = f(kv.get("lastClosePrice"))
        out["open"]   = f(kv.get("openPrice"))
        out["high"]   = f(kv.get("highPrice"))
        out["low"]    = f(kv.get("lowPrice"))
        out["volume"] = f(kv.get("accumulatedTradingVolume"))
        out["value"]  = f_won(kv.get("accumulatedTradingValue"))   # '4조 6,867억'
        out["market_cap"] = f_won(kv.get("marketValue"))           # '1,521조 4,940억'
        out["per"]    = f(kv.get("per"))                           # '11.67배'
        out["pbr"]    = f(kv.get("pbr"))                           # '3.02배'
        out["high52"] = f(kv.get("highPriceOf52Weeks"))
        out["low52"]  = f(kv.get("lowPriceOf52Weeks"))
    else:
        errors.append(f"integration failed: {code} (OHLC 없음, 현재가는 정상)")

    return out


# ---------------------------------------------------------------- 지수
def fetch_index(key, label):
    d = get_json(f"https://m.stock.naver.com/api/index/{key}/basic")
    if not d:
        return {"name": label, "error": "fetch_failed"}
    quote_time = d.get("localTradedAt")
    return {
        "name": label,
        "price": f(d.get("closePrice")),
        "change": f(d.get("compareToPreviousClosePrice")),
        "change_pct": f(d.get("fluctuationsRatio")),
        "market_status": d.get("marketStatus"),
        "quote_time": quote_time,
        "trade_date": trade_date_of(quote_time),
    }


# ---------------------------------------------------------------- 환율
# 순서 = 성공 확률 순. front-api 가 현재 유일하게 확인된 경로이므로 맨 앞.
# (m.stock .../api/marketindex/... 는 404 로 폐기됐다. 되살아날 수 있으니 후보로만 남긴다.)
FX_ENDPOINTS = [
    ("front","https://m.stock.naver.com/front-api/marketIndex/productDetail"
             "?category=exchange&reutersCode=FX_USDKRW"),
    ("api",  "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW"),
    ("m",    "https://m.stock.naver.com/api/marketindex/exchange/FX_USDKRW"),
]


def fetch_fx():
    """네이버 환율 엔드포인트는 자주 바뀐다. 후보를 순차 시도하고 어느 게 먹혔는지 기록."""
    for tag, url in FX_ENDPOINTS:
        d = get_json(url, tries=1, silent=True)
        if not d:
            continue
        node = d.get("result") if isinstance(d.get("result"), dict) else d
        for pk in ("closePrice", "closePrice2", "basePrice", "value"):
            px = f(node.get(pk)) if isinstance(node, dict) else None
            if px and 500 < px < 5000:          # 원/달러 상식 범위 검증
                return {
                    "pair": "USD/KRW",
                    "price": px,
                    "change_pct": f(node.get("fluctuationsRatio")),
                    "date": node.get("localTradedAt") or node.get("dt"),
                    "endpoint": tag,
                }
    errors.append("fx: 모든 엔드포인트 실패")
    return {"pair": "USD/KRW", "price": None, "error": "all_endpoints_failed"}


# ---------------------------------------------------------------- 해외
def fetch_overseas(tickers):
    if not tickers:
        return {}
    out = {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period="5d", progress=False,
                           group_by="ticker", threads=True, auto_adjust=False)
        for t in tickers:
            try:
                df = data[t] if len(tickers) > 1 else data
                df = df.dropna(subset=["Close"])
                if len(df) < 2:
                    out[t] = {"error": "insufficient_data"}
                    continue
                last, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
                out[t] = {
                    "price": round(last, 4),
                    "prev_close": round(prev, 4),
                    "change_pct": round((last / prev - 1) * 100, 2),
                    "asof": str(df.index[-1].date()),
                }
            except Exception as e:
                out[t] = {"error": f"{type(e).__name__}"}
                errors.append(f"yf {t}: {str(e)[:80]}")
    except Exception as e:
        errors.append(f"yfinance unavailable: {str(e)[:120]}")
    return out


# ---------------------------------------------------------------- main
def _load(path):
    """스냅샷 읽기. 없거나 깨졌으면 None."""
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None


def _coverage(payload):
    """스냅샷 충실도 점수 = 채워진 핵심 필드 개수. 덮어쓰기 판단에만 쓴다."""
    if not payload:
        return -1
    n = 0
    for v in (payload.get("domestic") or {}).values():
        n += sum(1 for k in ("price", "open", "high", "low", "volume")
                 if v.get(k) is not None)
    for v in (payload.get("index") or {}).values():
        n += 1 if v.get("price") is not None else 0
    return n


def main():
    cfg_path = os.path.join(ROOT, "tickers.json")
    with open(cfg_path, encoding="utf-8") as fp:
        cfg = json.load(fp)

    now = datetime.now(KST)

    payload = {
        "fetched_at_kst": now.isoformat(timespec="seconds"),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "epoch": int(time.time()),
        "source": "naver m.stock api (domestic) / yfinance (overseas)",
        "schema": 2,
        "trade_date": None,      # 체결 시각 기준 거래일. 수집일과 다를 수 있다(장 시작 전 수집)
        "market_open": None,     # 국내 장중 여부
        "domestic": {},
        "index": {},
        "fx": {},
        "overseas": {},
        "errors": [],
    }

    for code, name in cfg.get("domestic", {}).items():
        payload["domestic"][code] = fetch_domestic(code, name)
        time.sleep(0.4)

    for key, label in cfg.get("index", {}).items():
        payload["index"][key] = fetch_index(key, label)
        time.sleep(0.4)

    payload["fx"] = fetch_fx()
    payload["overseas"] = fetch_overseas(cfg.get("overseas", []))
    payload["errors"] = errors

    ok = sum(1 for v in payload["domestic"].values() if v.get("price") is not None)

    # 거래일은 체결 시각에서 뽑는다. 수집 시각(now)으로 정하면 장 시작 전 스냅샷의
    # 전일 종가가 당일 데이터로 둔갑한다.
    tds = [v.get("trade_date") for v in payload["domestic"].values() if v.get("trade_date")]
    tds += [v.get("trade_date") for v in payload["index"].values() if v.get("trade_date")]
    payload["trade_date"] = max(set(tds), key=tds.count) if tds else None
    payload["market_open"] = any(
        v.get("market_status") == "OPEN"
        for v in list(payload["domestic"].values()) + list(payload["index"].values())
    )

    payload["health"] = {
        "domestic_ok": ok,
        "domestic_total": len(payload["domestic"]),
        "error_count": len(errors),
        "trade_date": payload["trade_date"],
        "stale_date": bool(payload["trade_date"]) and payload["trade_date"] != f"{now:%Y-%m-%d}",
    }

    outdir = os.path.join(ROOT, "data")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "latest.json"), "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    # 일별 스냅샷.
    # 파일명은 수집일이 아니라 거래일이다. 07~09시(장 시작 전) 수집분은 전 거래일
    # 데이터를 담고 있으므로, 수집일로 이름 붙이면 당일 파일이 전일 값으로 채워진다.
    #
    # 단 그 스냅샷은 시/고/저/거래량이 비어 있다(장전에는 네이버가 안 준다).
    # 그래서 '이미 있는 스냅샷보다 채워진 필드가 적으면 덮어쓰지 않는다'.
    # 이 가드가 없으면 다음날 아침 수집이 전날의 완전한 종가 스냅샷을 깎아먹는다.
    snap_date = payload["trade_date"] or f"{now:%Y-%m-%d}"
    snap = os.path.join(outdir, f"{snap_date}.json")
    if _coverage(payload) >= _coverage(_load(snap)):
        with open(snap, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
    else:
        print(f"snapshot 유지: {snap_date} (기존 스냅샷이 더 완전함)")

    print(json.dumps(payload["health"], ensure_ascii=False))
    for e in errors[:10]:
        print("ERR:", e, file=sys.stderr)

    # 전부 실패했을 때만 워크플로 실패 처리
    if ok == 0 and payload["domestic"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

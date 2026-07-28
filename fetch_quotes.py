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


def get_json(url, tries=3):
    """재시도 포함 GET. 실패 시 None."""
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            errors.append(f"HTTP {r.status_code}: {url}")
        except Exception as e:
            errors.append(f"{type(e).__name__}: {url} :: {str(e)[:120]}")
        time.sleep(1.5 * (i + 1))
    return None


def f(v):
    """'254,000' / '-5.31' / None -> float | None"""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- 국내 종목
def fetch_domestic(code, name):
    d = get_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    if not d:
        return {"name": name, "error": "fetch_failed"}

    # 네이버는 장중이면 현재가, 장마감이면 종가를 closePrice 로 준다
    return {
        "name": d.get("stockName") or name,
        "price": f(d.get("closePrice")),
        "change": f(d.get("compareToPreviousClosePrice")),
        "change_pct": f(d.get("fluctuationsRatio")),
        "open": f(d.get("openPrice")),
        "high": f(d.get("highPrice")),
        "low": f(d.get("lowPrice")),
        "prev_close": f(d.get("previousClose")),
        "volume": f(d.get("accumulatedTradingVolume")),
        "value": f(d.get("accumulatedTradingValue")),
        "market_cap": f(d.get("marketValue")),
        "per": f(d.get("per")),
        "pbr": f(d.get("pbr")),
        # 네이버가 주는 상태 문자열. 'CLOSE' / 'OPEN' / 'PREOPEN' 등
        "market_status": d.get("marketStatus"),
        "quote_time": d.get("localTradedAt") or d.get("tradeStopYn"),
    }


# ---------------------------------------------------------------- 지수
def fetch_index(key, label):
    d = get_json(f"https://m.stock.naver.com/api/index/{key}/basic")
    if not d:
        return {"name": label, "error": "fetch_failed"}
    return {
        "name": label,
        "price": f(d.get("closePrice")),
        "change": f(d.get("compareToPreviousClosePrice")),
        "change_pct": f(d.get("fluctuationsRatio")),
        "market_status": d.get("marketStatus"),
        "quote_time": d.get("localTradedAt"),
    }


# ---------------------------------------------------------------- 환율
def fetch_fx():
    d = get_json("https://m.stock.naver.com/front-api/marketIndex/prices"
                 "?category=exchange&reutersCode=FX_USDKRW&page=1&pageSize=2")
    try:
        row = (d.get("result") or d.get("datas") or [])[0]
        return {
            "pair": "USD/KRW",
            "price": f(row.get("closePrice")),
            "change_pct": f(row.get("fluctuationsRatio")),
            "date": row.get("localTradedAt") or row.get("dt"),
        }
    except Exception:
        errors.append("fx: parse_failed")
        return {"pair": "USD/KRW", "error": "fetch_failed"}


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
        "schema": 1,
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
    payload["health"] = {
        "domestic_ok": ok,
        "domestic_total": len(payload["domestic"]),
        "error_count": len(errors),
    }

    outdir = os.path.join(ROOT, "data")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "latest.json"), "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    # 일별 스냅샷 (장마감 후 1회분이 덮어써지며 남는다)
    snap = os.path.join(outdir, f"{now:%Y-%m-%d}.json")
    with open(snap, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    print(json.dumps(payload["health"], ensure_ascii=False))
    for e in errors[:10]:
        print("ERR:", e, file=sys.stderr)

    # 전부 실패했을 때만 워크플로 실패 처리
    if ok == 0 and payload["domestic"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

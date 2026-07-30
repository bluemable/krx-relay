#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
semco_risk.py — 리스크 관리 계산기 (krx-relay 연동판)
 
데이터 우선순위
  1. krx-relay  data/history.jsonl   ← 종가 이력 (ATR 계산용)
  2. krx-relay  data/latest.json     ← 실시간/장중 현재가 + 신선도 검증
  3. FinanceDataReader / pykrx       ← 로컬 PC 에서만 동작 (클라우드는 프록시 차단)
 
relay 만으로도 돌아가므로 클라우드·로컬 어디서든 실행된다.
 
[실행]
  python semco_risk.py --account 50000000
  python semco_risk.py --code 005930 --account 100000000
  python semco_risk.py --relay https://raw.githubusercontent.com/OTHER/krx-relay/main/data
"""
 
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
 
KST = timezone(timedelta(hours=9))
 
RELAY_DEFAULT   = "https://raw.githubusercontent.com/bluemable/krx-relay/main/data"
ACCOUNT_RISK    = 0.02   # 2% 룰
ATR_N           = 14
INIT_STOP_MULT  = 2.0
TRAIL_MULT      = 3.0
TRAIL_LOOKBACK  = 22
MAX_WEIGHT      = 0.15   # 단일 종목 계좌 비중 상한
FRESH_LIMIT_MIN = 10     # relay README 기준
 
 
def fetch(url, timeout=20):
    """캐시버스팅 필수 — 없으면 CDN 이 최대 5분 된 사본을 준다."""
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(time.time())}",
                                 headers={"User-Agent": "semco-risk/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")
 
 
def load_history(relay, code):
    try:
        raw = fetch(f"{relay}/history.jsonl")
    except Exception as e:
        print(f"[정보] history.jsonl 없음 ({type(e).__name__}) — ATR 계산 불가")
        return []
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("code") == code and r.get("close") is not None:
            rows.append(r)
    # 같은 날짜 중복 시 마지막 것만
    dedup = {r["date"]: r for r in sorted(rows, key=lambda x: x["date"])}
    return [dedup[k] for k in sorted(dedup)]
 
 
def load_latest(relay, code):
    try:
        d = json.loads(fetch(f"{relay}/latest.json"))
    except Exception as e:
        print(f"[경고] latest.json 실패: {type(e).__name__}")
        return None, None
    s = (d.get("domestic") or {}).get(code)
    if not s:
        return None, d
    ts = datetime.fromisoformat(d["fetched_at_kst"])
    age = (datetime.now(KST) - ts).total_seconds() / 60
    s["_age_min"] = age
    s["_fetched"] = d["fetched_at_kst"]
    return s, d
 
 
def true_ranges(rows):
    """OHLC 가 다 있는 날만 TR 계산. 없는 날은 종가 기준 변화폭으로 대체하지 않는다."""
    trs = []
    for i in range(1, len(rows)):
        c, p = rows[i], rows[i - 1]
        if c.get("high") is None or c.get("low") is None:
            continue
        pc = p["close"]
        trs.append((c["date"],
                    max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc))))
    return trs
 
 
def wilder(values, n):
    if not values:
        return None
    a = values[0]
    for v in values[1:]:
        a += (v - a) / n
    return a
 
 
def won(x):
    return f"{int(round(x)):,}원" if x is not None else "—"
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="009150")
    ap.add_argument("--account", type=float, default=50_000_000)
    ap.add_argument("--entry", type=float, default=None)
    ap.add_argument("--relay", default=RELAY_DEFAULT)
    ap.add_argument("--atr", type=float, default=None, help="ATR 직접 지정(원)")
    a = ap.parse_args()
 
    print("=" * 66)
    hist = load_history(a.relay, a.code)
    live, _ = load_latest(a.relay, a.code)
 
    if live is None and not hist:
        sys.exit("데이터를 가져오지 못했습니다. --relay 주소를 확인하세요.")
 
    # ── 현재가 ─────────────────────────────────────────────
    if live:
        price = live["price"]
        stale = live["_age_min"] > FRESH_LIMIT_MIN
        flag = "STALE ⚠" if stale else "FRESH ✓"
        print(f" {live.get('name')} ({a.code})   [{live.get('market_status')}]  {flag}")
        print("=" * 66)
        print(f"  현재가        {won(price)}   {live.get('change_pct'):+.2f}%")
        if live.get("open"):
            tr_today = max(live["high"] - live["low"],
                           abs(live["high"] - (price - live["change"])),
                           abs(live["low"] - (price - live["change"])))
            print(f"  시/고/저      {live['open']:,.0f} / {live['high']:,.0f} / {live['low']:,.0f}")
            print(f"  당일 TR       {won(tr_today)}  ({tr_today/price:.1%})")
            print(f"  거래량        {live['volume']:,.0f}주")
        if live.get("high52"):
            print(f"  52주 최고     {won(live['high52'])}   현재 {price/live['high52']-1:+.1%}")
        print(f"  수집 시각     {live['_fetched']}  ({live['_age_min']:.0f}분 전)")
        if stale:
            print(f"  ** {FRESH_LIMIT_MIN}분 초과 — relay README 기준 '사용 불가'. 참고용으로만 보십시오.")
    else:
        price = hist[-1]["close"]
        print(f" {a.code}  (latest.json 없음 — history 최종 종가 사용)")
        print("=" * 66)
        print(f"  종가          {won(price)}   ({hist[-1]['date']})")
 
    entry = a.entry or price
 
    # ── ATR ────────────────────────────────────────────────
    print(f"\n[ 변동성 ]   이력 {len(hist)}일 보유")
    if a.atr:
        atr, atr_note = a.atr, "사용자 지정"
    else:
        trs = true_ranges(hist)
        if len(trs) >= 3:
            atr = wilder([v for _, v in trs], min(ATR_N, len(trs)))
            atr_note = f"ATR({min(ATR_N,len(trs))}) · TR {len(trs)}개"
            if len(trs) < ATR_N:
                atr_note += f"  ** {ATR_N}일 미만 — 표본 부족"
            print("  최근 TR: " + ", ".join(f"{d[5:]} {v:,.0f}" for d, v in trs[-5:]))
        else:
            atr, atr_note = None, "TR 표본 부족"
 
    if atr is None:
        print("  ATR 계산 불가. history.jsonl 이 최소 4영업일 쌓여야 합니다.")
        print("  임시로 --atr 값을 직접 넣어 실행하십시오.  예) --atr 240000\n")
        return
 
    print(f"  ATR           {won(atr)}   (주가의 {atr/price:.1%})   [{atr_note}]")
 
    # ── 2% 룰 ──────────────────────────────────────────────
    stop = entry - INIT_STOP_MULT * atr
    risk = entry - stop
    budget = a.account * ACCOUNT_RISK
    qty = int(budget // risk)
 
    print(f"\n[ 2% 룰 ]")
    print(f"  계좌          {won(a.account)}      허용 손실 {won(budget)}")
    print(f"  진입가        {won(entry)}")
    print(f"  초기 손절     {won(stop)}   ({stop/entry-1:+.1%}, 진입가 -{INIT_STOP_MULT}×ATR)")
    print(f"  주당 리스크   {won(risk)}")
    print(f"  ── 수량       {qty}주")
 
    if qty == 0:
        print(f"\n  ** 2% 룰로는 1주도 매수 불가. 최소 계좌 {won(risk/ACCOUNT_RISK)} 필요.")
        print("     손절폭을 줄여 맞추지 마십시오 — 일중 변동폭에 걸려 반드시 털립니다.")
    else:
        cost = qty * entry
        print(f"  투입액        {won(cost)}  (계좌의 {cost/a.account:.1%})")
        print(f"  실제 리스크   {qty*risk/a.account:.2%}")
        if cost / a.account > MAX_WEIGHT:
            print(f"  ** 비중 상한 {MAX_WEIGHT:.0%} 초과 → {int(a.account*MAX_WEIGHT//entry)}주로 축소 권장")
 
        print(f"\n[ 추적 손절 로드맵 ]   R = {won(risk)}")
        print(f"  ① 진입 즉시   손절 {won(stop)}")
        print(f"  ② +1R {won(entry+risk)}  → 손절을 {won(entry)}(본전)으로. 이후 리스크 0")
        print(f"  ③ +2R {won(entry+2*risk)}  → {qty//2}주 익절, 잔량 Chandelier 추적")
        print(f"  ④ 20일선 종가 이탈 또는 Chandelier 터치 → 전량 청산")
 
    # ── Chandelier ─────────────────────────────────────────
    highs = [r["high"] for r in hist[-TRAIL_LOOKBACK:] if r.get("high") is not None]
    if live and live.get("high"):
        highs.append(live["high"])
    if highs:
        peak = max(highs)
        ce = peak - TRAIL_MULT * atr
        print(f"\n[ Chandelier Exit ]  최근 고점 {won(peak)} − {TRAIL_MULT}×ATR")
        print(f"  추적 손절선   {won(ce)}   (현재가 대비 {ce/price-1:+.1%})")
        print("  주가가 오를 때만 따라 올라가고, 내릴 때는 절대 내리지 않습니다.")
 
    # ── 거래량 법칙 ────────────────────────────────────────
    vs = [r for r in hist if r.get("volume") and r.get("change_pct") is not None]
    if len(vs) >= 4:
        up = [r["volume"] for r in vs if r["change_pct"] > 0]
        dn = [r["volume"] for r in vs if r["change_pct"] < 0]
        if up and dn:
            u, d = sum(up)/len(up), sum(dn)/len(dn)
            ratio = u / d
            verdict = ("법칙A — 매수 우위 · 눌림목 쪽" if ratio > 1.1
                       else "법칙B — 분산/투매 쪽" if ratio < 0.9 else "중립")
            print(f"\n[ 거래량 법칙 ]  상승일 {u:,.0f}주 / 하락일 {d:,.0f}주 = {ratio:.2f}배")
            print(f"  → {verdict}")
    else:
        print(f"\n[ 거래량 법칙 ]  표본 부족 ({len(vs)}일). 최소 4영업일 필요.")
 
    print("\n  계산 도구이며 매매 권유가 아닙니다.\n")
 
 
if __name__ == "__main__":
    main()
 
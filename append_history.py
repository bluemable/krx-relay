#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
append_history.py — 장 마감 종가를 data/history.jsonl 에 한 줄씩 누적한다.
 
krx-relay 저장소 루트에 두고, close.yml 워크플로에서 fetch_quotes.py 직후에 실행한다.
 
설계 원칙 (fetch_quotes.py 와 동일하게 유지)
  1. 숫자를 창작하지 않는다. 필수 필드가 없으면 그 종목은 건너뛴다.
  2. 멱등(idempotent). 같은 (date, code) 가 이미 있으면 다시 쓰지 않는다.
     -> 워크플로가 하루에 여러 번 돌아도 안전하다.
  3. 장중 데이터는 절대 넣지 않는다. market_status 가 CLOSE 인 것만 받는다.
     -> 이게 없으면 14:36 짜리 미확정 시세가 종가로 굳어버린다.
 
출력 형식 (JSON Lines, 1줄 = 1종목 1일)
  {"date":"2026-07-28","code":"009150","name":"삼성전기",
   "open":1301000,"high":1301000,"low":1081000,"close":1114000,
   "volume":1510160,"change_pct":-15.92,"captured_at":"..."}
"""
 
import json
import os
import sys
from datetime import datetime, timedelta, timezone
 
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
LATEST = os.path.join(DATA, "latest.json")
HIST = os.path.join(DATA, "history.jsonl")
 
# 종가로 인정할 최소 조건
REQUIRED = ("open", "high", "low", "volume")
 
 
def load_existing_keys(path):
    """이미 기록된 (date, code) 집합. 손상된 줄은 조용히 건너뛴다."""
    keys = set()
    if not os.path.exists(path):
        return keys
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                keys.add((r["date"], r["code"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return keys
 
 
def main():
    if not os.path.exists(LATEST):
        print("append_history: data/latest.json 없음 — 건너뜀")
        return 0
 
    with open(LATEST, encoding="utf-8") as fp:
        payload = json.load(fp)
 
    fetched = payload.get("fetched_at_kst")
    if not fetched:
        print("append_history: fetched_at_kst 없음 — 중단", file=sys.stderr)
        return 0
 
    ts = datetime.fromisoformat(fetched)
    date = ts.strftime("%Y-%m-%d")
 
    existing = load_existing_keys(HIST)
    rows, skipped = [], []
 
    # ---- 국내 종목 ----
    for code, s in (payload.get("domestic") or {}).items():
        if (date, code) in existing:
            skipped.append(f"{code}:이미기록")
            continue
 
        status = s.get("market_status")
        if status != "CLOSE":
            skipped.append(f"{code}:장중({status})")
            continue
 
        if s.get("price") is None or any(s.get(k) is None for k in REQUIRED):
            skipped.append(f"{code}:필드누락")
            continue
 
        rows.append({
            "date": date,
            "code": code,
            "name": s.get("name"),
            "open": s["open"],
            "high": s["high"],
            "low": s["low"],
            "close": s["price"],
            "volume": s["volume"],
            "change_pct": s.get("change_pct"),
            "quote_time": s.get("quote_time"),
            "captured_at": fetched,
            "source": "relay",
        })
 
    # ---- 지수 (OHLC 없음. 종가만) ----
    for key, v in (payload.get("index") or {}).items():
        if (date, key) in existing:
            continue
        if v.get("market_status") != "CLOSE" or v.get("price") is None:
            continue
        rows.append({
            "date": date,
            "code": key,
            "name": v.get("name"),
            "open": None, "high": None, "low": None,
            "close": v["price"],
            "volume": None,
            "change_pct": v.get("change_pct"),
            "captured_at": fetched,
            "source": "relay",
        })
 
    if not rows:
        print(f"append_history: 추가 없음 ({date}) — {', '.join(skipped) or '해당 없음'}")
        return 0
 
    os.makedirs(DATA, exist_ok=True)
    with open(HIST, "a", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")
 
    print(f"append_history: {date} — {len(rows)}건 추가 "
          f"({', '.join(r['code'] for r in rows)})")
    if skipped:
        print(f"  건너뜀: {', '.join(skipped)}")
    return 0
 
 
if __name__ == "__main__":
    sys.exit(main())
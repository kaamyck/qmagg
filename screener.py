#!/usr/bin/env python3
"""
Qullamaggie Setup Screener v2 — S&P 500 + NASDAQ
Setup breakout/flag: momentum burst -> baza 5-65 dni -> zaciesnienie -> pivot.
v2: dluzsze bazy (do 65 sesji), ranking RS 0-99 vs cale uniwersum (1/3/6M),
filtr rezimu rynku (QQQ/SPY vs EMA10/20), daty wynikow dla finalistow.
"""

import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

# ————— DZWIGNIE (levers) — krec tutaj —————
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 10_000_000   # sredni dzienny obrot USD
MIN_ADR = 3.5                 # minimalny ADR20 % (Qullamaggie: 3.5-4+)
MIN_MOVE = 30.0               # minimalny ruch % (okno 30/60/90 sesji)
BASE_MIN_DAYS = 5             # minimalna dlugosc bazy
BASE_MAX_DAYS = 65            # maksymalna dlugosc bazy (~3 miesiace)
BASE_MAX_DEPTH = 0.75         # cena >= 75% pivota (max 25% glebokosc bazy)
RS_GATE_SETUP = 90            # minimalny RS (0-99) dla statusu SETUP
SETUP_MIN_SCORE = 68
TOP_N = 250
EARNINGS_LOOKUP_TOP = 120     # dla ilu najlepszych sprawdzic date wynikow
# ————————————————————————————————————————————

HISTORY_MONTHS = "9mo"
CHUNK = 200


def get_sp500() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=30)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    return tables[0]["Symbol"].astype(str).str.strip().tolist()


def get_nasdaq() -> list[str]:
    url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    df = df[df["Symbol"].notna()]
    df = df[df["Test Issue"] == "N"]
    if "ETF" in df.columns:
        df = df[df["ETF"] != "Y"]
    syms = df["Symbol"].astype(str).str.strip()
    return [s for s in syms if s.isalpha() and len(s) <= 4 or (len(s) == 5 and s[-1] not in "WRU")]


def build_universe() -> list[str]:
    syms: set[str] = set()
    try:
        sp = get_sp500()
        syms.update(sp)
        print(f"S&P 500: OK ({len(sp)})")
    except Exception as e:
        print(f"S&P 500 blad: {e}", file=sys.stderr)
    try:
        nas = get_nasdaq()
        syms.update(nas)
        print(f"NASDAQ: +{len(nas)}")
    except Exception as e:
        print(f"NASDAQ blad: {e}", file=sys.stderr)
    return sorted({s.replace(".", "-") for s in syms if s and s.upper() == s})


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def weighted_return(c: np.ndarray) -> float | None:
    """Wazona stopa zwrotu 1/3/6M (proxy IBD RS): 3*1M + 2*3M + 1*6M."""
    n = len(c)
    if n < 126 or np.isnan(c[-126:]).any():
        if n < 63 or np.isnan(c[-63:]).any():
            return None
        r1 = c[-1] / c[-21] - 1
        r3 = c[-1] / c[-63] - 1
        return 3 * r1 + 2 * r3
    r1 = c[-1] / c[-21] - 1
    r3 = c[-1] / c[-63] - 1
    r6 = c[-1] / c[-126] - 1
    return 3 * r1 + 2 * r3 + r6


def analyze(o, h, l, c, v) -> tuple[dict | None, float | None]:
    """Zwraca (kandydat lub None, wret do rankingu RS lub None)."""
    n = len(c)
    if n < 70 or np.isnan(c[-70:]).any():
        return None, None

    price = float(c[-1])
    if price < MIN_PRICE:
        return None, None
    dollar_vol = float(np.nanmean(c[-20:] * v[-20:]))
    if dollar_vol < MIN_DOLLAR_VOL:
        return None, None

    wret = weighted_return(c)

    e10, e20 = ema(c, 10), ema(c, 20)
    s50 = float(np.nanmean(c[-50:]))

    best_move, best_win = 0.0, 0
    for w in (30, 60, 90):
        lo = float(np.nanmin(c[-min(w, n):]))
        move = (price / lo - 1) * 100
        if move > best_move:
            best_move, best_win = move, w

    adr20 = float(np.nanmean(h[-20:] / l[-20:] - 1) * 100)
    adr5 = float(np.nanmean(h[-5:] / l[-5:] - 1) * 100)
    adr60 = float(np.nanmean(h[-60:] / l[-60:] - 1) * 100)
    adr120 = float(np.nanmean(h[-min(120, n):] / l[-min(120, n):] - 1) * 100)  # charakter spolki (obejmuje faze ruchu)
    contraction = adr5 / adr20 if adr20 > 0 else 1.0

    above_mas = price > e10[-1] and price > e20[-1] and price > s50
    ma_stack = e10[-1] > e20[-1]
    ma_rising = e10[-1] > e10[-6] and e20[-1] > e20[-6]

    # pivot: max high z ostatnich BASE_MAX_DAYS sesji bez dzisiejszej (v2: dluzsze bazy)
    lb = min(BASE_MAX_DAYS + 1, n - 1)
    look_h = h[-(lb + 1):-1]
    pivot = float(np.nanmax(look_h))
    days_since_high = int(len(look_h) - np.nanargmax(look_h))
    dist_to_pivot = (pivot / price - 1) * 100
    broke_down = price < pivot * BASE_MAX_DEPTH

    consol = max(min(days_since_high, 15), 3)
    lows = l[-consol:]
    half = consol // 2
    higher_lows = bool(np.nanmean(lows[half:]) >= np.nanmean(lows[:half]) * 0.995)

    vol5 = float(np.nanmean(v[-5:]))
    vol20 = float(np.nanmean(v[-20:]))
    vol_dry = vol5 / vol20 if vol20 > 0 else 1.0

    breakout_today = price > pivot and c[-2] <= pivot
    chg_today = (price / c[-2] - 1) * 100

    # twarde filtry kandydata (RS liczymy dla wszystkich plynnych — stad wret oddzielnie)
    adr_char = max(adr20, adr60, adr120)
    ma_ok = price > s50 * 0.97 and price > e20[-1] * 0.97  # tolerancja 3% na faze bazy
    if best_move < 25 or not ma_ok or broke_down or adr_char < MIN_ADR * 0.8:
        return None, wret

    score = 0.0
    score += min(25, max(0, (best_move - 20) / 80 * 25 + (8 if best_move >= MIN_MOVE else 0)))
    score += 15 if adr_char >= 6 else (8 + (adr_char - MIN_ADR) / 2.5 * 7 if adr_char >= MIN_ADR else adr_char / MIN_ADR * 6)
    score += (10 if above_mas else 0) + (5 if ma_stack else 0) + (5 if ma_rising else 0)
    score += 20 if contraction <= 0.75 else 12 if contraction <= 0.9 else 6 if contraction <= 1.0 else 0
    score += 10 if vol_dry <= 0.7 else 6 if vol_dry <= 0.9 else 3 if vol_dry <= 1.0 else 0
    score += 10 if 0 <= dist_to_pivot <= 4 else 6 if dist_to_pivot <= 8 else 3 if dist_to_pivot <= 12 else 0
    score = round(min(100, max(0, score)))

    cand = {
        "score": int(score),
        "price": round(float(price), 2), "chgToday": round(float(chg_today), 2),
        "bestMove": round(float(best_move), 1), "bestWin": int(best_win),
        "adr20": round(float(adr20), 2), "contraction": round(float(contraction), 2),
        "daysSinceHigh": int(days_since_high), "distToPivot": round(float(dist_to_pivot), 2),
        "volDry": round(float(vol_dry), 2), "higherLows": bool(higher_lows),
        "aboveMAs": bool(above_mas), "maStack": bool(ma_stack),
        "pivot": round(float(pivot), 2), "dollarVol": round(float(dollar_vol) / 1e6, 1),
        "breakoutToday": bool(breakout_today),
        "spark": [round(float(x), 2) for x in c[-60:] if not np.isnan(x)],
    }
    return cand, wret


def market_regime():
    """QQQ i SPY vs EMA10/20 — filtr rezimu Qullamaggie."""
    import yfinance as yf
    out = {}
    try:
        data = yf.download(["QQQ", "SPY"], period="3mo", interval="1d",
                           group_by="ticker", auto_adjust=True, progress=False)
        for idx in ("QQQ", "SPY"):
            c = data[idx]["Close"].dropna().to_numpy(float)
            e10, e20 = ema(c, 10)[-1], ema(c, 20)[-1]
            p = c[-1]
            out[idx] = "risk-on" if (p > e10 and p > e20) else "neutral" if p > e20 else "risk-off"
    except Exception as e:
        print(f"regime blad: {e}", file=sys.stderr)
    return out


def earnings_days(symbols: list[str]) -> dict[str, int]:
    """Dni do najblizszych wynikow (tylko dla finalistow)."""
    import yfinance as yf
    out = {}
    today = pd.Timestamp.now(tz="UTC").normalize()
    for sym in symbols:
        try:
            cal = yf.Ticker(sym).calendar
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if dates:
                d = pd.Timestamp(dates[0])
                d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
                delta = (d.normalize() - today).days
                if -1 <= delta <= 45:
                    out[sym] = int(delta)
        except Exception:
            pass
        time.sleep(0.15)
    return out


def scan(symbols: list[str]):
    import yfinance as yf
    candidates, rs_pool = [], []
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        print(f"chunk {i // CHUNK + 1}/{(len(symbols) - 1) // CHUNK + 1} ({len(chunk)})")
        try:
            data = yf.download(chunk, period=HISTORY_MONTHS, interval="1d",
                               group_by="ticker", auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"  chunk blad: {e}", file=sys.stderr)
            continue
        for sym in chunk:
            try:
                df = data[sym].dropna(how="all") if len(chunk) > 1 else data.dropna(how="all")
                if df.empty:
                    continue
                cand, wret = analyze(
                    df["Open"].to_numpy(float), df["High"].to_numpy(float),
                    df["Low"].to_numpy(float), df["Close"].to_numpy(float),
                    df["Volume"].to_numpy(float),
                )
                if wret is not None:
                    rs_pool.append((sym, wret))
                if cand:
                    cand["sym"] = sym
                    candidates.append(cand)
            except Exception:
                continue
        time.sleep(2)
    return candidates, rs_pool


def main() -> None:
    universe = build_universe()
    print(f"Uniwersum: {len(universe)} tickerow")
    candidates, rs_pool = scan(universe)

    # ranking RS 0-99 vs wszystkie plynne spolki
    rets = pd.Series({s: r for s, r in rs_pool})
    ranks = (rets.rank(pct=True) * 99).round().astype(int)
    for c in candidates:
        c["rs"] = int(ranks.get(c["sym"], 0))

    # statusy (v2: RS gate dla SETUP)
    results = []
    for c in candidates:
        if c["breakoutToday"] and c["bestMove"] >= 25 and c["rs"] >= RS_GATE_SETUP - 10:
            c["status"] = "BREAKOUT"
        elif (c["score"] >= SETUP_MIN_SCORE and c["distToPivot"] <= 8
              and BASE_MIN_DAYS <= c["daysSinceHigh"] <= BASE_MAX_DAYS
              and c["rs"] >= RS_GATE_SETUP):
            c["status"] = "SETUP"
        elif c["score"] >= 45 and c["daysSinceHigh"] >= 2 and c["rs"] >= 70:
            c["status"] = "BUDUJE"
        else:
            continue
        del c["breakoutToday"]
        results.append(c)

    results.sort(key=lambda r: (r["score"], r["rs"]), reverse=True)
    results = results[:TOP_N]

    # daty wynikow dla czolowki
    top_syms = [r["sym"] for r in results[:EARNINGS_LOOKUP_TOP]]
    print(f"Sprawdzam daty wynikow dla {len(top_syms)} spolek…")
    er = earnings_days(top_syms)
    for r in results:
        if r["sym"] in er:
            r["daysToER"] = er[r["sym"]]

    regime = market_regime()
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": len(universe),
        "regime": regime,
        "counts": {
            "BREAKOUT": sum(r["status"] == "BREAKOUT" for r in results),
            "SETUP": sum(r["status"] == "SETUP" for r in results),
            "BUDUJE": sum(r["status"] == "BUDUJE" for r in results),
        },
        "results": results,
    }
    os.makedirs("docs", exist_ok=True)
    with open("docs/results.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Zapisano {len(results)} wynikow (SETUP {out['counts']['SETUP']}, "
          f"BREAKOUT {out['counts']['BREAKOUT']}, BUDUJE {out['counts']['BUDUJE']}), "
          f"regime: {regime}")


if __name__ == "__main__":
    main()

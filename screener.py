#!/usr/bin/env python3
"""
Qullamaggie Setup Screener — S&P 500 + NASDAQ
Skanuje cale uniwersum po sesji i zapisuje docs/results.json.
Kryteria: momentum burst (>=30% w 30/60/90 sesji), ADR20 >= 3.5%,
cena nad EMA10/EMA20/SMA50, zaciesniajaca sie baza 3-15 dni,
wysychajacy wolumen, cena blisko pivota.
"""

import io
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 10_000_000  # sredni dzienny obrot USD
HISTORY_MONTHS = "9mo"
CHUNK = 200
TOP_N = 250  # ile wynikow zapisac do JSON


# ---------------------------------------------------------------- universe

def get_sp500() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)
    syms = tables[0]["Symbol"].astype(str).str.strip().tolist()
    return syms


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
    # odrzuc warranty/prawa poboru/jednostki (5-znakowe z sufiksem W/R/U)
    syms = [s for s in syms if s.isalpha() and len(s) <= 4 or (len(s) == 5 and s[-1] not in "WRU")]
    return list(syms)


def build_universe() -> list[str]:
    syms: set[str] = set()
    try:
        syms.update(get_sp500())
        print(f"S&P 500: OK ({len(syms)})")
    except Exception as e:
        print(f"S&P 500 blad: {e}", file=sys.stderr)
    try:
        nas = get_nasdaq()
        syms.update(nas)
        print(f"NASDAQ: +{len(nas)}")
    except Exception as e:
        print(f"NASDAQ blad: {e}", file=sys.stderr)
    # format Yahoo: kropki -> myslniki (BRK.B -> BRK-B)
    return sorted({s.replace(".", "-") for s in syms if s and s.upper() == s})


# ---------------------------------------------------------------- analytics

def ema(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def analyze(o, h, l, c, v) -> dict | None:
    n = len(c)
    if n < 70 or np.isnan(c[-70:]).any():
        return None

    price = float(c[-1])
    if price < MIN_PRICE:
        return None
    dollar_vol = float(np.nanmean(c[-20:] * v[-20:]))
    if dollar_vol < MIN_DOLLAR_VOL:
        return None

    e10, e20 = ema(c, 10), ema(c, 20)
    s50 = float(np.nanmean(c[-50:]))

    # momentum burst
    best_move, best_win = 0.0, 0
    for w in (30, 60, 90):
        lo = float(np.nanmin(c[-min(w, n):]))
        move = (price / lo - 1) * 100
        if move > best_move:
            best_move, best_win = move, w

    adr20 = float(np.nanmean(h[-20:] / l[-20:] - 1) * 100)
    adr5 = float(np.nanmean(h[-5:] / l[-5:] - 1) * 100)
    contraction = adr5 / adr20 if adr20 > 0 else 1.0

    above_mas = price > e10[-1] and price > e20[-1] and price > s50
    ma_stack = e10[-1] > e20[-1]
    ma_rising = e10[-1] > e10[-6] and e20[-1] > e20[-6]

    # pivot: max high z ostatnich 25 sesji bez dzisiejszej
    look_h = h[-26:-1]
    pivot = float(np.nanmax(look_h))
    days_since_high = int(len(look_h) - np.nanargmax(look_h))
    dist_to_pivot = (pivot / price - 1) * 100
    broke_down = price < pivot * 0.82

    consol = max(min(days_since_high, 12), 3)
    lows = l[-consol:]
    half = consol // 2
    higher_lows = bool(np.nanmean(lows[half:]) >= np.nanmean(lows[:half]) * 0.995)

    vol5 = float(np.nanmean(v[-5:]))
    vol20 = float(np.nanmean(v[-20:]))
    vol_dry = vol5 / vol20 if vol20 > 0 else 1.0

    breakout_today = price > pivot and c[-2] <= pivot
    chg_today = (price / c[-2] - 1) * 100

    # scoring 0-100
    score = 0.0
    score += min(25, max(0, (best_move - 20) / 80 * 25 + (8 if best_move >= 30 else 0)))
    score += 15 if adr20 >= 6 else (8 + (adr20 - 3.5) / 2.5 * 7 if adr20 >= 3.5 else adr20 / 3.5 * 6)
    score += (10 if above_mas else 0) + (5 if ma_stack else 0) + (5 if ma_rising else 0)
    score += 20 if contraction <= 0.75 else 12 if contraction <= 0.9 else 6 if contraction <= 1.0 else 0
    score += 10 if vol_dry <= 0.7 else 6 if vol_dry <= 0.9 else 3 if vol_dry <= 1.0 else 0
    score += 10 if 0 <= dist_to_pivot <= 4 else 6 if dist_to_pivot <= 8 else 3 if dist_to_pivot <= 12 else 0
    score = round(min(100, max(0, score)))

    if breakout_today and best_move >= 25 and above_mas:
        status = "BREAKOUT"
    elif broke_down or best_move < 25 or not above_mas:
        status = "BRAK"
    elif score >= 68 and dist_to_pivot <= 8 and days_since_high >= 3:
        status = "SETUP"
    elif score >= 45 and days_since_high >= 2:
        status = "BUDUJE"
    else:
        status = "BRAK"

    if status == "BRAK":
        return None  # nie zasmiecamy JSON-a

    spark = [round(float(x), 2) for x in c[-60:] if not np.isnan(x)]

    return {
        "score": int(score), "status": status,
        "price": round(float(price), 2), "chgToday": round(float(chg_today), 2),
        "bestMove": round(float(best_move), 1), "bestWin": int(best_win),
        "adr20": round(float(adr20), 2), "contraction": round(float(contraction), 2),
        "daysSinceHigh": int(days_since_high), "distToPivot": round(float(dist_to_pivot), 2),
        "volDry": round(float(vol_dry), 2), "higherLows": bool(higher_lows),
        "aboveMAs": bool(above_mas), "maStack": bool(ma_stack),
        "pivot": round(float(pivot), 2), "dollarVol": round(float(dollar_vol) / 1e6, 1),
        "spark": spark,
    }


# ---------------------------------------------------------------- scan

def scan(symbols: list[str]) -> list[dict]:
    import yfinance as yf

    results = []
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        print(f"chunk {i // CHUNK + 1}/{(len(symbols) - 1) // CHUNK + 1} ({len(chunk)})")
        try:
            data = yf.download(
                chunk, period=HISTORY_MONTHS, interval="1d",
                group_by="ticker", auto_adjust=True,
                threads=True, progress=False,
            )
        except Exception as e:
            print(f"  chunk blad: {e}", file=sys.stderr)
            continue
        for sym in chunk:
            try:
                df = data[sym].dropna(how="all") if len(chunk) > 1 else data.dropna(how="all")
                if df.empty:
                    continue
                r = analyze(
                    df["Open"].to_numpy(float), df["High"].to_numpy(float),
                    df["Low"].to_numpy(float), df["Close"].to_numpy(float),
                    df["Volume"].to_numpy(float),
                )
                if r:
                    r["sym"] = sym
                    results.append(r)
            except Exception:
                continue
        time.sleep(2)
    return results


def main() -> None:
    universe = build_universe()
    print(f"Uniwersum: {len(universe)} tickerow")
    results = scan(universe)
    results.sort(key=lambda r: r["score"], reverse=True)

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": len(universe),
        "counts": {
            "BREAKOUT": sum(r["status"] == "BREAKOUT" for r in results),
            "SETUP": sum(r["status"] == "SETUP" for r in results),
            "BUDUJE": sum(r["status"] == "BUDUJE" for r in results),
        },
        "results": results[:TOP_N],
    }
    with open("docs/results.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Zapisano {len(out['results'])} wynikow "
          f"(SETUP {out['counts']['SETUP']}, BREAKOUT {out['counts']['BREAKOUT']}, "
          f"BUDUJE {out['counts']['BUDUJE']})")


if __name__ == "__main__":
    main()

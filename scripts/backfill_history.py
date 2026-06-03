#!/usr/bin/env python3
"""
Backfill signal history CSV with 6 months of historical data.

Uses the same shared signal detection logic as the live pipeline,
but scans a longer historical window and writes all completed signals
to data/signal_history.csv.

This is meant to be run ONCE to seed the history, then the live pipeline
appends new signals on each run.

Usage:
    python3 scripts/backfill_history.py                    # 6 months, all tickers
    python3 scripts/backfill_history.py --months 12        # 12 months
    python3 scripts/backfill_history.py --clear             # wipe and rebuild
"""

import csv
import math
import sys
import time
import yaml
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import yfinance as yf
import pandas as pd

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
HISTORY_CSV = ROOT / "data" / "signal_history.csv"

from signals import (
    compute_rsi, compute_sma, compute_ema,
    detect_signals_at, detect_catalyst_days,
    load_sector_map, SECTOR_ETFS, STRONG_TYPES,
)

LOOKAHEAD_DAYS = 14
COOLDOWN_DAYS = 5
BATCH_SIZE = 50

FIELDS = [
    "date", "ticker", "type", "direction", "detail",
    "price", "entry_price", "result_5", "result_10",
    "current_pct", "max_drawdown_pct", "days_to_5", "days_to_10",
]


def load_watchlist():
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    return [str(t) for t in config["watchlist"]]


def evaluate_signal(closes, opens, highs, lows, signal_idx, lookahead=14):
    """Check if a signal at signal_idx hit +5%/+10% within lookahead days."""
    if signal_idx >= len(closes) - 1:
        return None  # Can't evaluate without future data

    entry_price = opens[signal_idx + 1]
    end_idx = min(signal_idx + 1 + lookahead, len(closes) - 1)
    days_elapsed = end_idx - (signal_idx + 1)
    is_complete = days_elapsed >= lookahead

    if not is_complete:
        return None  # Only want completed signals for history

    future_highs = highs[signal_idx+1:end_idx+1]
    future_lows = lows[signal_idx+1:end_idx+1]
    future_closes = closes[signal_idx+1:end_idx+1]

    if not future_closes:
        return None

    latest_price = future_closes[-1]
    current_pct = ((latest_price - entry_price) / entry_price) * 100

    max_dd = 0
    for lo in future_lows:
        dd = ((lo - entry_price) / entry_price) * 100
        if dd < max_dd:
            max_dd = dd

    hit_5 = hit_10 = False
    days_to_5 = days_to_10 = None
    for i, h in enumerate(future_highs):
        pct = ((h - entry_price) / entry_price) * 100
        if not hit_5 and pct >= 5:
            hit_5 = True
            days_to_5 = i + 1
        if not hit_10 and pct >= 10:
            hit_10 = True
            days_to_10 = i + 1

    return {
        "result_5": "win" if hit_5 else "loss",
        "result_10": "win" if hit_10 else "loss",
        "current_pct": round(current_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "entry_price": round(entry_price, 2),
        "days_to_5": days_to_5,
        "days_to_10": days_to_10,
    }


def fetch_prices(tickers, months):
    """Batch-download price data."""
    period = f"{months}mo"
    all_data = {}
    batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]

    for i, batch in enumerate(batches):
        print(f"  Batch {i+1}/{len(batches)}: downloading {len(batch)} tickers...")
        try:
            raw = yf.download(batch, period=period, interval="1d",
                              group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"    Batch failed: {e}")
            continue

        if raw.empty:
            continue

        for ticker in batch:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if ticker in raw.columns.get_level_values(0):
                        df = raw[ticker].copy()
                    else:
                        continue
                else:
                    df = raw.copy()

                df = df.reset_index()
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                col_map = {c: c.lower() for c in df.columns}
                df = df.rename(columns=col_map)
                df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
                cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
                df = df[cols].dropna()
                if len(df) >= 30:
                    all_data[ticker] = df
            except Exception:
                pass

        if i < len(batches) - 1:
            time.sleep(1)

    print(f"  Fetched {len(all_data)} tickers successfully")
    return all_data


def scan_all_signals(all_data):
    """Scan entire history for all tickers, return completed buy signals."""
    print("Scanning historical signals...")

    # Detect catalyst days for earnings_drift
    catalyst_days = detect_catalyst_days(all_data)
    print(f"  Found catalyst days for {len(catalyst_days)} tickers")

    # Compute SPY returns for adjusted signals
    spy_returns = {}
    spy_data = all_data.pop("SPY", None)
    if spy_data is not None:
        spy_closes = spy_data["close"].tolist()
        spy_dates = spy_data["date"].tolist()
        for i in range(1, len(spy_closes)):
            date_key = str(spy_dates[i])[:10]
            spy_returns[date_key] = ((spy_closes[i] - spy_closes[i-1]) / spy_closes[i-1]) * 100
        print(f"  SPY benchmark: {len(spy_data)} days")

    # Compute sector ETF returns
    sector_returns = {}
    for etf in list(SECTOR_ETFS):
        sdf = all_data.pop(etf, None)
        if sdf is not None:
            s_closes = sdf["close"].tolist()
            s_dates = sdf["date"].tolist()
            etf_rets = {}
            for i in range(1, len(s_closes)):
                date_key = str(s_dates[i])[:10]
                etf_rets[date_key] = ((s_closes[i] - s_closes[i-1]) / s_closes[i-1]) * 100
            sector_returns[etf] = etf_rets
    print(f"  Sector benchmarks: {len(sector_returns)} ETFs")

    sector_map = load_sector_map()

    all_signals = []
    for ticker_idx, (ticker, df) in enumerate(all_data.items()):
        if (ticker_idx + 1) % 50 == 0:
            print(f"  Processing {ticker_idx + 1}/{len(all_data)}...")

        closes = df["close"].tolist()
        opens = df["open"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        volumes = df["volume"].tolist()
        dates = df["date"].tolist()
        n = len(df)

        cooldown_tracker = defaultdict(list)

        # Start from day 35 (need enough history for MACD) and stop LOOKAHEAD_DAYS from end
        for idx in range(35, n - LOOKAHEAD_DAYS):
            sigs = detect_signals_at(closes, opens, highs, lows, volumes, idx)

            # Adjusted return signals
            if spy_returns and idx >= 1:
                date_key = str(dates[idx])[:10]
                spy_ret = spy_returns.get(date_key)
                if spy_ret is not None:
                    stock_ret = ((closes[idx] - closes[idx-1]) / closes[idx-1]) * 100
                    adj_ret = stock_ret - spy_ret
                    if adj_ret <= -5.0:
                        in_downtrend = False
                        if idx >= 54:
                            sma50 = compute_sma(closes[:idx+1], 50)
                            below_count = sum(1 for i in range(idx-4, idx+1)
                                            if not math.isnan(sma50[i]) and closes[i] < sma50[i])
                            in_downtrend = below_count >= 5
                        if not in_downtrend:
                            sigs.append({
                                "type": "adjusted_drop", "direction": "buy",
                                "detail": f"Market-adjusted drop {adj_ret:+.2f}% "
                                          f"(stock {stock_ret:+.2f}%, market {spy_ret:+.2f}%)",
                            })
                    elif adj_ret >= 5.0:
                        sigs.append({
                            "type": "adjusted_surge", "direction": "sell",
                            "detail": f"Market-adjusted surge +{adj_ret:.2f}%",
                        })

            # Sector-adjusted drop: stock vs its own sector ETF
            if sector_returns and idx >= 1:
                sector_etf = sector_map.get(ticker)
                if sector_etf and sector_etf in sector_returns:
                    date_key = str(dates[idx])[:10]
                    sector_ret = sector_returns[sector_etf].get(date_key)
                    if sector_ret is not None:
                        stock_ret_s = ((closes[idx] - closes[idx-1]) / closes[idx-1]) * 100
                        sector_adj = stock_ret_s - sector_ret
                        if sector_adj <= -5.0:
                            in_downtrend = False
                            if idx >= 54:
                                sma50 = compute_sma(closes[:idx+1], 50)
                                below_count = sum(1 for i in range(idx-4, idx+1)
                                                if not math.isnan(sma50[i]) and closes[i] < sma50[i])
                                in_downtrend = below_count >= 5
                            if not in_downtrend:
                                sigs.append({
                                    "type": "sector_drop", "direction": "buy",
                                    "detail": f"Sector-adjusted drop {sector_adj:+.2f}% "
                                              f"(stock {stock_ret_s:+.2f}%, {sector_etf} {sector_ret:+.2f}%)",
                                })
                        elif sector_adj >= 5.0:
                            sigs.append({
                                "type": "sector_surge", "direction": "sell",
                                "detail": f"Sector-adjusted surge +{sector_adj:.2f}%",
                            })

            # Post-catalyst drift
            if ticker in catalyst_days and (idx - 1) in catalyst_days[ticker] and idx >= 2:
                drift_ret = ((closes[idx] - closes[idx-1]) / closes[idx-1]) * 100
                catalyst_gap = ((opens[idx-1] - closes[idx-2]) / closes[idx-2]) * 100
                if catalyst_gap > 0 and drift_ret >= 0:
                    sigs.append({
                        "type": "earnings_drift", "direction": "buy",
                        "detail": f"Post-catalyst drift: gap {catalyst_gap:+.1f}%, move {drift_ret:+.1f}%",
                    })
                elif catalyst_gap < 0 and drift_ret <= 0:
                    sigs.append({
                        "type": "earnings_drift", "direction": "sell",
                        "detail": f"Post-catalyst drop: gap {catalyst_gap:+.1f}%, move {drift_ret:+.1f}%",
                    })

            # Apply cooldown and evaluate
            emitted_buy_sigs = []
            for s in sigs:
                if s["direction"] != "buy":
                    continue

                key = (ticker, s["type"])
                recent = [i for i in cooldown_tracker[key] if idx - i <= COOLDOWN_DAYS]
                if recent:
                    continue
                cooldown_tracker[key].append(idx)

                ev = evaluate_signal(closes, opens, highs, lows, idx, LOOKAHEAD_DAYS)
                if ev is None:
                    continue

                all_signals.append({
                    "date": str(dates[idx])[:10],
                    "ticker": ticker,
                    "type": s["type"],
                    "direction": "buy",
                    "detail": s["detail"],
                    "price": round(closes[idx], 2),
                    **ev,
                })
                emitted_buy_sigs.append(s)

            # Confluence
            strong_buy = [s for s in emitted_buy_sigs if s["type"] in STRONG_TYPES]
            if len(strong_buy) >= 2:
                conf_key = (ticker, "confluence")
                recent_conf = [i for i in cooldown_tracker[conf_key] if idx - i <= COOLDOWN_DAYS]
                if not recent_conf:
                    cooldown_tracker[conf_key].append(idx)
                    ev = evaluate_signal(closes, opens, highs, lows, idx, LOOKAHEAD_DAYS)
                    if ev:
                        components = ", ".join(s["type"] for s in strong_buy)
                        all_signals.append({
                            "date": str(dates[idx])[:10],
                            "ticker": ticker,
                            "type": "confluence",
                            "direction": "buy",
                            "detail": f"Confluence ({len(strong_buy)}): {components}",
                            "price": round(closes[idx], 2),
                            **ev,
                        })

    print(f"  Generated {len(all_signals)} completed buy signals")
    return all_signals


def write_csv(signals, clear=False):
    """Write signals to history CSV."""
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)

    if clear and HISTORY_CSV.exists():
        HISTORY_CSV.unlink()
        print(f"  Cleared {HISTORY_CSV}")

    # Deduplicate against existing
    existing_keys = set()
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, "r") as f:
            for row in csv.DictReader(f):
                existing_keys.add((row["date"], row["ticker"], row["type"]))

    new_rows = []
    for s in signals:
        key = (s["date"], s["ticker"], s["type"])
        if key not in existing_keys:
            new_rows.append({f: s.get(f, "") for f in FIELDS})
            existing_keys.add(key)

    write_header = not HISTORY_CSV.exists() or HISTORY_CSV.stat().st_size == 0
    with open(HISTORY_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"  Wrote {len(new_rows)} new rows (total: {len(existing_keys)})")


def main():
    parser = argparse.ArgumentParser(description="Backfill signal history")
    parser.add_argument("--months", type=int, default=6, help="Months of history (default: 6)")
    parser.add_argument("--clear", action="store_true", help="Clear existing history first")
    args = parser.parse_args()

    print(f"Backfilling {args.months} months of signal history...")
    watchlist = load_watchlist()
    extra = ["SPY"] + [e for e in SECTOR_ETFS if e not in watchlist]
    tickers = watchlist + [e for e in extra if e not in watchlist]
    print(f"  {len(tickers)} tickers (including SPY + {len(SECTOR_ETFS)} sector ETFs)")

    all_data = fetch_prices(tickers, args.months)
    if not all_data:
        print("No data. Exiting.")
        sys.exit(1)

    signals = scan_all_signals(all_data)
    write_csv(signals, clear=args.clear)

    # Quick summary
    from collections import Counter
    types = Counter(s["type"] for s in signals)
    print(f"\nBackfill complete:")
    print(f"  Total signals: {len(signals)}")
    for t, c in types.most_common():
        wins = sum(1 for s in signals if s["type"] == t and s["result_5"] == "win")
        wr = wins / c * 100 if c > 0 else 0
        print(f"    {t:20s} {c:5d}  wr5={wr:.1f}%")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch 1 year of daily data for all watchlist tickers + SPY, compute adjusted returns, and run historical backfill."""
import sys
sys.path.insert(0, "/Users/ufuk/market-monitor")

import yaml
import yfinance as yf
import pandas as pd
import numpy as np
from pathlib import Path
from data.db import get_connection, init_db, store_daily, compute_adjusted_returns
from signals.technical import check_rsi, check_macd_crossover, check_ma_crossover
from signals.price import check_percent_change
from signals.volume import check_volume_spike

COOLDOWN_DAYS = 5

config_path = Path("/Users/ufuk/market-monitor/config.yaml")
with open(config_path) as f:
    config = yaml.safe_load(f)

tickers = config["watchlist"]
# Always include SPY as market benchmark
all_tickers = list(set(tickers + ["SPY"]))
print(f"Watchlist: {len(tickers)} tickers + SPY benchmark")

# Step 1: Fresh database
db_path = Path("/Users/ufuk/market-monitor/market.duckdb")
wal_path = db_path.with_suffix(".duckdb.wal")
if db_path.exists():
    db_path.unlink()
if wal_path.exists():
    wal_path.unlink()
print("Cleared old database")

init_db()
print("Initialized fresh database")

# Step 2: Fetch 1 year of daily data in batches
BATCH_SIZE = 50
total_rows = 0

for i in range(0, len(all_tickers), BATCH_SIZE):
    batch = all_tickers[i:i+BATCH_SIZE]
    batch_num = i // BATCH_SIZE + 1
    total_batches = (len(all_tickers) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\nBatch {batch_num}/{total_batches}: downloading {len(batch)} tickers...")

    try:
        data = yf.download(batch, period="1y", interval="1d", group_by="ticker", progress=False, threads=True)
    except Exception as e:
        print(f"  Error downloading batch: {e}")
        continue

    for ticker in batch:
        try:
            if len(batch) > 1:
                df = data[ticker].dropna(how="all")
            else:
                df = data.dropna(how="all")
            if df.empty:
                continue
            store_daily(ticker, df)
            total_rows += len(df)
        except Exception as e:
            print(f"  {ticker}: {e}")

    print(f"  Stored. Running total: {total_rows:,} rows")

print(f"\nTotal daily price rows: {total_rows:,}")

# Step 3: Compute adjusted returns (now uses close-to-close + SPY benchmark)
print("\nComputing adjusted returns...")
compute_adjusted_returns()
print("Done")

# Step 4: Historical signal backfill with cooldown and confluence
print("\nRunning historical signal backfill...")
sig_config = config["signals"]
con = get_connection()

signals_count = 0
# Only backfill watchlist tickers, not SPY
for ti, ticker in enumerate(tickers):
    df = con.execute("""
        SELECT date, open, high, low, close, adj_close, volume,
               day_return_pct, market_return_pct, adjusted_return_pct
        FROM daily_prices
        WHERE ticker = ?
        ORDER BY date
    """, [ticker]).fetchdf()

    if len(df) < 60:
        continue

    # Track last signal date per type for cooldown
    last_fired = {}

    for day_idx in range(60, len(df)):
        window = df.iloc[day_idx-59:day_idx+1].copy()
        window = window.reset_index(drop=True)
        signal_date = df.iloc[day_idx]["date"]
        close_price = df.iloc[day_idx]["close"]

        batch_signals = []

        def on_cooldown(sig_type):
            if sig_type not in last_fired:
                return False
            delta = (pd.Timestamp(signal_date) - pd.Timestamp(last_fired[sig_type])).days
            return delta < COOLDOWN_DAYS

        # Percent change (volatility-normalized, needs full window)
        if sig_config["percent_change"]["enabled"] and not on_cooldown("percent_change"):
            result = check_percent_change(
                window,
                daily_threshold=sig_config["percent_change"]["daily_threshold"]
            )
            if result:
                batch_signals.append(("percent_change", result[0], result[1]))

        # Technical indicators
        if sig_config["technical"]["enabled"]:
            tc = sig_config["technical"]
            if not on_cooldown("rsi"):
                rsi_result = check_rsi(window, tc["rsi"]["period"], tc["rsi"]["oversold"], tc["rsi"]["overbought"])
                if rsi_result:
                    batch_signals.append(("rsi", rsi_result[0], rsi_result[1]))

            if not on_cooldown("macd"):
                macd_result = check_macd_crossover(window, tc["macd"]["fast"], tc["macd"]["slow"], tc["macd"]["signal"])
                if macd_result:
                    batch_signals.append(("macd", macd_result[0], macd_result[1]))

            if len(window) >= tc["moving_average_crossover"]["slow_period"] + 1 and not on_cooldown("ma_crossover"):
                ma_result = check_ma_crossover(
                    window,
                    tc["moving_average_crossover"]["fast_period"],
                    tc["moving_average_crossover"]["slow_period"]
                )
                if ma_result:
                    batch_signals.append(("ma_crossover", ma_result[0], ma_result[1]))

        # Volume spike (now directional)
        if sig_config["volume"]["enabled"] and len(window) >= 21 and not on_cooldown("volume_spike"):
            vol_result = check_volume_spike(window, sig_config["volume"]["spike_multiplier"])
            if vol_result:
                batch_signals.append(("volume_spike", vol_result[0], vol_result[1]))

        # Adjusted return
        if sig_config["adjusted_return"]["enabled"]:
            adj_cfg = sig_config["adjusted_return"]
            adj_ret = df.iloc[day_idx]["adjusted_return_pct"]
            if adj_ret is not None and not pd.isna(adj_ret):
                day_ret = df.iloc[day_idx]["day_return_pct"]
                mkt_ret = df.iloc[day_idx]["market_return_pct"]
                if adj_ret <= adj_cfg["drop_threshold"] and not on_cooldown("adjusted_drop"):
                    batch_signals.append(("adjusted_drop", "buy",
                        f"Market-adjusted drop {adj_ret:+.2f}% (stock {day_ret:+.2f}%, market {mkt_ret:+.2f}%)"))
                elif adj_ret >= adj_cfg["surge_threshold"] and not on_cooldown("adjusted_surge"):
                    batch_signals.append(("adjusted_surge", "sell",
                        f"Market-adjusted surge +{adj_ret:.2f}% (stock {day_ret:+.2f}%, market {mkt_ret:+.2f}%)"))

        for sig_type, direction, details in batch_signals:
            con.execute("""
                INSERT INTO signals_fired (ticker, timestamp, signal_type, direction, price_at_signal, details)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [ticker, str(signal_date), sig_type, direction, float(close_price), details])
            signals_count += 1
            last_fired[sig_type] = signal_date

        # Confluence: 2+ buy signals on same day
        buy_sigs = [s for s in batch_signals if s[1] == "buy"]
        if len(buy_sigs) >= 2:
            components = ", ".join(s[0] for s in buy_sigs)
            detail = f"Confluence ({len(buy_sigs)} signals): {components}"
            con.execute("""
                INSERT INTO signals_fired (ticker, timestamp, signal_type, direction, price_at_signal, details)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [ticker, str(signal_date), "confluence", "buy", float(close_price), detail])
            signals_count += 1

    if (ti + 1) % 10 == 0:
        print(f"  Processed {ti+1}/{len(tickers)} tickers, {signals_count:,} signals so far")

con.close()
print(f"\nBackfill complete: {signals_count:,} signals generated across {len(tickers)} tickers")

# Step 5: Rebuild validation cache (SQL-based, fast)
print("\nRebuilding validation cache...")
from backtest.validator import rebuild_cache
val_count, wr_count = rebuild_cache()
print(f"Cached: {val_count:,} validated signals, {wr_count:,} win rate rows")

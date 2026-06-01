#!/usr/bin/env python3
"""
Generate live signals JSON for the RapidSift dashboard.

Fetches 3 months of price data from Yahoo Finance, runs signal detection
over recent trading days, evaluates win/loss for signals with enough
lookahead data, and writes JSON to _site/data/signals.json.

This runs on GitHub Actions alongside the Telegram notifier. On each push,
Vercel auto-deploys the updated JSON so the dashboard always shows fresh data.

Usage:
    python3 scripts/generate_signals_json.py
"""

import json
import math
import sys
import time
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import yfinance as yf
import pandas as pd

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "_site" / "data"
CONFIG_PATH = ROOT / "config.yaml"

BATCH_SIZE = 50             # Download tickers in batches of 50
MAX_RETRIES = 1             # Retry failed tickers once
RETRY_WAIT = 5              # Seconds to wait before retry

SIGNAL_SCAN_DAYS = 14       # Scan the last N trading days for signals
LOOKAHEAD_DAYS = 14         # Check win/loss over next N calendar days
COOLDOWN_DAYS = 5           # De-duplicate: same signal type+ticker within N days


def load_watchlist():
    """Load watchlist from config.yaml (single source of truth)."""
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    return [str(t) for t in config["watchlist"]]


WATCHLIST = load_watchlist()


# ── Signal Detection (mirrors notify_telegram.py) ──────────────────────────

def compute_rsi(closes, period=14):
    deltas = [0.0] + [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [-min(d, 0) for d in deltas]
    rsi = [float('nan')] * len(closes)
    if len(closes) < period + 1:
        return rsi
    avg_gain = sum(gains[1:period+1]) / period
    avg_loss = sum(losses[1:period+1]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100 - (100 / (1 + avg_gain / avg_loss))
    return rsi


def compute_sma(closes, period):
    sma = [float('nan')] * len(closes)
    for i in range(period - 1, len(closes)):
        sma[i] = sum(closes[i - period + 1:i + 1]) / period
    return sma


def compute_ema(data, period):
    ema = [data[0]]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        ema.append(data[i] * k + ema[-1] * (1 - k))
    return ema


def detect_signals_at(closes, opens, highs, lows, volumes, idx):
    """
    Detect signals at a given index (treating closes[:idx+1] as the history).
    Returns list of signal dicts (without ticker/date, those are added later).
    """
    signals = []
    if idx < 1:
        return signals

    latest_close = closes[idx]
    prev_close = closes[idx - 1]

    # Percent change (volatility-normalized)
    pct = ((latest_close - prev_close) / prev_close) * 100
    if idx >= 21:
        rets = [(closes[i] - closes[i-1]) / closes[i-1] * 100
                for i in range(max(1, idx - 19), idx + 1)]
        mean_r = sum(rets) / len(rets)
        std = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
        if std > 0 and abs(pct / std) >= 2.0:
            z = pct / std
            signals.append({
                "type": "percent_change",
                "direction": "sell" if pct > 0 else "buy",
                "detail": f"Daily move {pct:+.2f}% ({z:+.1f}σ)",
            })
    elif abs(pct) >= 3.0:
        signals.append({
            "type": "percent_change",
            "direction": "sell" if pct > 0 else "buy",
            "detail": f"Daily move {pct:+.2f}%",
        })

    # RSI transition
    rsi = compute_rsi(closes[:idx+1], 14)
    if len(rsi) >= 2 and not math.isnan(rsi[-1]) and not math.isnan(rsi[-2]):
        if rsi[-1] <= 30 and rsi[-2] > 30:
            signals.append({"type": "rsi", "direction": "buy",
                           "detail": f"RSI={rsi[-1]:.1f} crossed below 30"})
        elif rsi[-1] >= 70 and rsi[-2] < 70:
            signals.append({"type": "rsi", "direction": "sell",
                           "detail": f"RSI={rsi[-1]:.1f} crossed above 70"})

    # MACD crossover
    c = closes[:idx+1]
    if len(c) >= 35:
        ema12 = compute_ema(c, 12)
        ema26 = compute_ema(c, 26)
        macd = [ema12[i] - ema26[i] for i in range(len(c))]
        sig_line = compute_ema(macd, 9)
        prev_d = macd[-2] - sig_line[-2]
        curr_d = macd[-1] - sig_line[-1]
        if prev_d < 0 and curr_d > 0:
            signals.append({"type": "macd", "direction": "buy", "detail": "MACD bullish crossover"})
        elif prev_d > 0 and curr_d < 0:
            signals.append({"type": "macd", "direction": "sell", "detail": "MACD bearish crossover"})

    # MA crossover
    if len(c) >= 51:
        sma20 = compute_sma(c, 20)
        sma50 = compute_sma(c, 50)
        if not any(math.isnan(v) for v in [sma20[-1], sma20[-2], sma50[-1], sma50[-2]]):
            prev_d = sma20[-2] - sma50[-2]
            curr_d = sma20[-1] - sma50[-1]
            if prev_d < 0 and curr_d > 0:
                signals.append({"type": "ma_crossover", "direction": "buy",
                               "detail": "SMA20 crossed above SMA50"})
            elif prev_d > 0 and curr_d < 0:
                signals.append({"type": "ma_crossover", "direction": "sell",
                               "detail": "SMA20 crossed below SMA50"})

    # Volume spike
    if idx >= 20:
        avg_vol = sum(volumes[idx-20:idx]) / 20
        if avg_vol > 0 and volumes[idx] / avg_vol >= 2.0:
            ratio = volumes[idx] / avg_vol
            direction = "buy" if latest_close < opens[idx] else "sell"
            signals.append({"type": "volume_spike", "direction": direction,
                           "detail": f"Volume spike: {ratio:.1f}x average"})

    return signals


# ── Win/loss evaluation ────────────────────────────────────────────────────

def evaluate_signal(closes, opens, highs, lows, signal_idx, lookahead=14):
    """
    Given a signal at signal_idx, check the next `lookahead` trading days.
    Entry price is the next trading day's open (you can't buy at the close).
    Returns dict with result_5, result_10, current_pct, max_drawdown_pct,
    days_to_5, days_to_10, and entry_price.
    """
    signal_close = closes[signal_idx]

    # Need at least one day after signal to have an entry
    if signal_idx >= len(closes) - 1:
        return {"result_5": "pending", "result_10": "pending",
                "current_pct": 0, "max_drawdown_pct": 0, "latest_price": signal_close,
                "entry_price": None, "days_to_5": None, "days_to_10": None}

    # Entry at next day's open (matches DuckDB validator behavior)
    entry_price = opens[signal_idx + 1]
    end_idx = min(signal_idx + 1 + lookahead, len(closes) - 1)

    future_closes = closes[signal_idx+1:end_idx+1]
    future_highs = highs[signal_idx+1:end_idx+1]
    future_lows = lows[signal_idx+1:end_idx+1]

    if not future_closes:
        return {"result_5": "pending", "result_10": "pending",
                "current_pct": 0, "max_drawdown_pct": 0, "latest_price": signal_close,
                "entry_price": round(entry_price, 2), "days_to_5": None, "days_to_10": None}

    latest_price = future_closes[-1]
    current_pct = ((latest_price - entry_price) / entry_price) * 100

    # Max drawdown from entry
    max_dd = 0
    for lo in future_lows:
        dd = ((lo - entry_price) / entry_price) * 100
        if dd < max_dd:
            max_dd = dd

    # Check if hit +5% / +10% and track days to hit
    days_to_5 = None
    days_to_10 = None
    hit_5 = False
    hit_10 = False
    for i, h in enumerate(future_highs):
        pct = ((h - entry_price) / entry_price) * 100
        if not hit_5 and pct >= 5:
            hit_5 = True
            days_to_5 = i + 1  # 1-based: day 1 is the first day after signal
        if not hit_10 and pct >= 10:
            hit_10 = True
            days_to_10 = i + 1

    days_elapsed = end_idx - (signal_idx + 1)
    is_complete = days_elapsed >= lookahead

    result_5 = ("win" if hit_5 else ("loss" if is_complete else "pending"))
    result_10 = ("win" if hit_10 else ("loss" if is_complete else "pending"))

    return {
        "result_5": result_5,
        "result_10": result_10,
        "current_pct": round(current_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "latest_price": round(latest_price, 2),
        "entry_price": round(entry_price, 2),
        "days_to_5": days_to_5,
        "days_to_10": days_to_10,
    }


# ── Sparkline SVG builder ──────────────────────────────────────────────────

def build_sparkline_svg(closes, signal_idx, uid):
    """Build a mini sparkline SVG (96x28) for the 14d window around a signal."""
    W, H = 96, 28
    MARGIN = 2

    # Take up to 12 days before signal + signal day + up to 14 days after
    start = max(0, signal_idx - 12)
    end = min(len(closes), signal_idx + 15)
    window = closes[start:end]
    sig_pos = signal_idx - start  # position of signal in window

    n = len(window)
    if n < 2:
        return ""

    vmin = min(window) * 0.998
    vmax = max(window) * 1.002
    vrange = vmax - vmin or 1

    def px(i): return round(MARGIN + (i + 0.5) / n * (W - 2 * MARGIN), 1)
    def py(v): return round(MARGIN + (H - 2 * MARGIN) * (1 - (v - vmin) / vrange), 1)

    entry = window[sig_pos]
    last = window[-1]
    pct_change = ((last - entry) / entry) * 100
    is_up = pct_change >= 0
    color = "oklch(0.80 0.165 145)" if is_up else "oklch(0.72 0.185 25)"
    grad_color = color

    pts = " ".join(f"{px(i)},{py(v)}" for i, v in enumerate(window))

    # Entry line
    entry_y = py(entry)

    svg = f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" style="display:block;overflow:visible">'
    svg += f'<defs><linearGradient id="sg{uid}" x1="0" x2="0" y1="0" y2="1">'
    svg += f'<stop offset="0%" stop-color="{grad_color}" stop-opacity=".25"/>'
    svg += f'<stop offset="100%" stop-color="{grad_color}" stop-opacity="0"/>'
    svg += '</linearGradient></defs>'

    # Fill area
    fill_pts = pts + f" {px(n-1)},{H} {px(0)},{H}"
    svg += f'<path d="M{fill_pts.replace(",", " ").replace("  ", " ")}" fill="url(#sg{uid})"/>'

    # Entry line
    svg += f'<line x1="{MARGIN}" x2="{W-MARGIN}" y1="{entry_y}" y2="{entry_y}" '
    svg += f'stroke="oklch(0.30 0.010 250 / 0.45)" stroke-width="0.5" stroke-dasharray="2 2"/>'

    # Price line
    svg += f'<polyline points="{pts}" fill="none" stroke="{color}" '
    svg += f'stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/>'

    # End dot
    lx, ly = px(n-1), py(last)
    svg += f'<circle cx="{lx}" cy="{ly}" r="3" fill="{color}" opacity=".18"/>'
    svg += f'<circle cx="{lx}" cy="{ly}" r="1.6" fill="{color}"/>'

    svg += '</svg>'
    return svg


# ── Main pipeline ──────────────────────────────────────────────────────────

def _parse_batch_data(raw_data, batch, all_data, errors):
    """Extract per-ticker DataFrames from a yf.download() result."""
    for ticker in batch:
        try:
            if len(batch) > 1:
                df = raw_data[ticker].dropna(how="all")
            else:
                # Single-ticker batch: may have MultiIndex columns
                if isinstance(raw_data.columns, pd.MultiIndex):
                    df = raw_data[ticker].dropna(how="all")
                else:
                    df = raw_data.dropna(how="all")
            if df.empty:
                errors.append(f"{ticker}: empty")
                continue
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[cols].dropna()
            if len(df) >= 5:
                all_data[ticker] = df
        except Exception as e:
            errors.append(f"{ticker}: {e}")


def fetch_all_prices():
    """Fetch 3mo of daily prices using batched yf.download() for speed."""
    # Include SPY for adjusted return signals
    fetch_list = WATCHLIST + (["SPY"] if "SPY" not in WATCHLIST else [])
    print(f"Fetching prices for {len(fetch_list)} tickers in batches of {BATCH_SIZE}...")
    all_data = {}
    errors = []
    failed_tickers = []

    total_batches = (len(fetch_list) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(fetch_list), BATCH_SIZE):
        batch = fetch_list[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  Batch {batch_num}/{total_batches}: downloading {len(batch)} tickers...")
        try:
            raw = yf.download(batch, period="3mo", interval="1d",
                              group_by="ticker", progress=False, threads=True)
            before = len(all_data)
            _parse_batch_data(raw, batch, all_data, errors)
            # Track tickers that didn't make it into all_data
            for t in batch:
                if t not in all_data:
                    failed_tickers.append(t)
            print(f"    Got {len(all_data) - before} tickers from this batch")
        except Exception as e:
            print(f"    Batch download error: {e}")
            failed_tickers.extend(batch)

    # Retry failed tickers once after a short wait
    if failed_tickers and MAX_RETRIES > 0:
        retry_list = [t for t in failed_tickers if t not in all_data]
        if retry_list:
            print(f"  Retrying {len(retry_list)} failed tickers after {RETRY_WAIT}s...")
            time.sleep(RETRY_WAIT)
            for rb_start in range(0, len(retry_list), BATCH_SIZE):
                retry_batch = retry_list[rb_start:rb_start + BATCH_SIZE]
                try:
                    raw = yf.download(retry_batch, period="3mo", interval="1d",
                                      group_by="ticker", progress=False, threads=True)
                    _parse_batch_data(raw, retry_batch, all_data, errors)
                except Exception as e:
                    print(f"    Retry batch error: {e}")

    # Report success rate
    final_failed = [t for t in fetch_list if t not in all_data]
    success_rate = (len(all_data) / len(fetch_list)) * 100 if fetch_list else 0
    print(f"  Success: {len(all_data)}/{len(fetch_list)} tickers ({success_rate:.0f}%)")
    if final_failed:
        print(f"  Failed ({len(final_failed)}): {final_failed[:20]}")
    return all_data


def scan_signals(all_data, spy_data=None):
    """
    Scan last SIGNAL_SCAN_DAYS trading days for each ticker.
    Returns list of signal dicts sorted by date descending.
    spy_data: optional dict with SPY price data for adjusted return signals.
    """
    print(f"Scanning for signals (last {SIGNAL_SCAN_DAYS} days)...")

    all_signals = []
    # Track signal history per (ticker, type) for cooldown dedup
    cooldown_tracker = defaultdict(list)  # (ticker, type) -> list of signal_idx

    # Pre-compute SPY daily returns keyed by date string for adjusted signals
    spy_returns = {}
    if spy_data is not None:
        spy_closes = spy_data["close"].tolist()
        spy_dates = spy_data["date"].tolist()
        for i in range(1, len(spy_closes)):
            date_key = str(spy_dates[i])[:10]
            spy_returns[date_key] = ((spy_closes[i] - spy_closes[i-1]) / spy_closes[i-1]) * 100

    for ticker, df in all_data.items():
        closes = df["close"].tolist()
        opens = df["open"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        volumes = df["volume"].tolist()
        dates = df["date"].tolist()
        n = len(df)

        # Scan the full range to build cooldown history, but only emit recent ones
        scan_start = max(1, n - 60)  # Look back 60 days for cooldown tracking
        emit_start = max(1, n - SIGNAL_SCAN_DAYS)

        for idx in range(scan_start, n):
            sigs = detect_signals_at(closes, opens, highs, lows, volumes, idx)

            # Adjusted return signals (requires SPY data)
            if spy_returns and idx >= 1:
                date_key = str(dates[idx])[:10]
                spy_ret = spy_returns.get(date_key)
                if spy_ret is not None:
                    stock_ret = ((closes[idx] - closes[idx-1]) / closes[idx-1]) * 100
                    adj_ret = stock_ret - spy_ret
                    if adj_ret <= -5.0:
                        sigs.append({
                            "type": "adjusted_drop",
                            "direction": "buy",
                            "detail": f"Market-adjusted drop {adj_ret:+.2f}% "
                                      f"(stock {stock_ret:+.2f}%, market {spy_ret:+.2f}%)",
                        })
                    elif adj_ret >= 5.0:
                        sigs.append({
                            "type": "adjusted_surge",
                            "direction": "sell",
                            "detail": f"Market-adjusted surge +{adj_ret:.2f}% "
                                      f"(stock {stock_ret:+.2f}%, market {spy_ret:+.2f}%)",
                        })

            # Collect emitted signals for this ticker+day for confluence detection
            emitted_buy_sigs = []

            for s in sigs:
                key = (ticker, s["type"])

                # Check cooldown: skip if same signal within COOLDOWN_DAYS
                recent = [i for i in cooldown_tracker[key] if idx - i <= COOLDOWN_DAYS]
                if recent:
                    continue

                cooldown_tracker[key].append(idx)

                # Only emit signals from the recent window
                if idx >= emit_start:
                    eval_result = evaluate_signal(closes, opens, highs, lows, idx, LOOKAHEAD_DAYS)
                    sparkline = build_sparkline_svg(closes, idx, f"{len(all_signals)}")

                    signal_date = str(dates[idx])[:10]
                    # Include detection timestamp (when GH Actions ran)
                    signal_time = datetime.utcnow().strftime("%H:%M")
                    sig_out = {
                        "ticker": ticker,
                        "date": signal_date,
                        "time": signal_time,
                        "type": s["type"],
                        "direction": s["direction"],
                        "detail": s["detail"],
                        "price": round(closes[idx], 2),
                        "entry_price": eval_result["entry_price"],
                        "result_5": eval_result["result_5"],
                        "result_10": eval_result["result_10"],
                        "current_pct": eval_result["current_pct"],
                        "latest_price": eval_result["latest_price"],
                        "max_drawdown_pct": eval_result["max_drawdown_pct"],
                        "days_to_5": eval_result["days_to_5"],
                        "days_to_10": eval_result["days_to_10"],
                        "sparkline": sparkline,
                    }
                    all_signals.append(sig_out)
                    if s["direction"] == "buy":
                        emitted_buy_sigs.append(sig_out)

            # Confluence: 2+ buy signals on the same ticker+day (mirrors engine.py)
            if len(emitted_buy_sigs) >= 2:
                conf_key = (ticker, "confluence")
                recent_conf = [i for i in cooldown_tracker[conf_key] if idx - i <= COOLDOWN_DAYS]
                if not recent_conf:
                    cooldown_tracker[conf_key].append(idx)
                    components = ", ".join(s["type"] for s in emitted_buy_sigs)
                    detail = f"Confluence ({len(emitted_buy_sigs)} signals): {components}"
                    eval_result = evaluate_signal(closes, opens, highs, lows, idx, LOOKAHEAD_DAYS)
                    sparkline = build_sparkline_svg(closes, idx, f"{len(all_signals)}")
                    signal_date = str(dates[idx])[:10]
                    all_signals.append({
                        "ticker": ticker,
                        "date": signal_date,
                        "time": datetime.utcnow().strftime("%H:%M"),
                        "type": "confluence",
                        "direction": "buy",
                        "detail": detail,
                        "price": round(closes[idx], 2),
                        "entry_price": eval_result["entry_price"],
                        "result_5": eval_result["result_5"],
                        "result_10": eval_result["result_10"],
                        "current_pct": eval_result["current_pct"],
                        "latest_price": eval_result["latest_price"],
                        "max_drawdown_pct": eval_result["max_drawdown_pct"],
                        "days_to_5": eval_result["days_to_5"],
                        "days_to_10": eval_result["days_to_10"],
                        "sparkline": sparkline,
                    })

    # Sort by date descending, then ticker
    all_signals.sort(key=lambda s: (s["date"], s["ticker"]), reverse=True)
    print(f"  Found {len(all_signals)} signals")
    return all_signals


def compute_win_rates(all_signals):
    """
    Compute win rates per (ticker, signal_type) from the signals we have.
    Returns dict: (ticker, type) -> {wr5, wr10, fired}
    """
    groups = defaultdict(list)
    for s in all_signals:
        key = (s["ticker"], s["type"])
        groups[key].append(s)

    stats = {}
    for key, sigs in groups.items():
        completed_5 = [s for s in sigs if s["result_5"] != "pending"]
        completed_10 = [s for s in sigs if s["result_10"] != "pending"]

        wr5 = (sum(1 for s in completed_5 if s["result_5"] == "win") / len(completed_5) * 100) if completed_5 else 0
        wr10 = (sum(1 for s in completed_10 if s["result_10"] == "win") / len(completed_10) * 100) if completed_10 else 0

        stats[key] = {"wr5": round(wr5, 1), "wr10": round(wr10, 1), "fired": len(sigs)}

    return stats


def compute_baseline(all_data, lookahead=LOOKAHEAD_DAYS):
    """Compute random-entry baseline: for every ticker+day, did a random buy hit +5%/+10%?"""
    total = 0
    hits_5 = 0
    hits_10 = 0

    for ticker, df in all_data.items():
        opens = df["open"].tolist()
        highs = df["high"].tolist()
        n = len(df)
        for idx in range(n - lookahead):
            entry = opens[idx + 1] if idx + 1 < n else opens[idx]
            if entry <= 0:
                continue
            total += 1
            future_highs = highs[idx + 1:idx + 1 + lookahead]
            if any(((h - entry) / entry) * 100 >= 5 for h in future_highs):
                hits_5 += 1
            if any(((h - entry) / entry) * 100 >= 10 for h in future_highs):
                hits_10 += 1

    if total == 0:
        return {"total": 0, "wr5": 0, "wr10": 0}
    return {
        "total": total,
        "wr5": round(hits_5 / total * 100, 1),
        "wr10": round(hits_10 / total * 100, 1),
    }


def build_json(all_signals, win_rates, baseline):
    """Build the final JSON structure."""
    buy_signals = [s for s in all_signals if s["direction"] == "buy"]

    signals_out = []
    for s in buy_signals:
        key = (s["ticker"], s["type"])
        wr = win_rates.get(key, {"wr5": 0, "wr10": 0, "fired": 1})

        sig_out = {
            "ticker": s["ticker"],
            "date": s["date"],
            "time": s.get("time", ""),
            "type": s["type"],
            "detail": s["detail"],
            "price": s["price"],
            "latest_price": s["latest_price"],
            "current_pct": s["current_pct"],
            "result_5": s["result_5"],
            "result_10": s["result_10"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "wr5": wr["wr5"],
            "wr10": wr["wr10"],
            "fired": wr["fired"],
            "sparkline": s["sparkline"],
        }
        # Include entry_price and days-to-hit if available
        if s.get("entry_price") is not None:
            sig_out["entry_price"] = s["entry_price"]
        if s.get("days_to_5") is not None:
            sig_out["days_to_5"] = s["days_to_5"]
        if s.get("days_to_10") is not None:
            sig_out["days_to_10"] = s["days_to_10"]
        signals_out.append(sig_out)

    # KPI summary
    total_buy = len(buy_signals)
    total_sell = len([s for s in all_signals if s["direction"] == "sell"])
    unique_tickers = len(set(s["ticker"] for s in buy_signals))
    avg_wr5 = 0
    completed = [s for s in signals_out if s["result_5"] != "pending"]
    if completed:
        avg_wr5 = round(sum(1 for s in completed if s["result_5"] == "win") / len(completed) * 100, 1)

    # Build reliability table: per (ticker, strategy) win rates sorted by wr5 desc
    reliability = []
    for (ticker, sig_type), wr in sorted(win_rates.items(), key=lambda x: -x[1]["wr5"]):
        reliability.append({
            "ticker": ticker,
            "type": sig_type,
            "fired": wr["fired"],
            "wr5": wr["wr5"],
            "wr10": wr["wr10"],
            "summary_5": f"{int(wr['wr5'] * wr['fired'] / 100 + 0.5)}/{wr['fired']} hit +5% ({wr['wr5']}%)",
            "summary_10": f"{int(wr['wr10'] * wr['fired'] / 100 + 0.5)}/{wr['fired']} hit +10% ({wr['wr10']}%)",
        })

    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "kpi": {
            "buy_signals": total_buy,
            "sell_signals": total_sell,
            "unique_tickers": unique_tickers,
            "avg_win_rate_5": avg_wr5,
        },
        "signals": signals_out,
        "reliability": reliability,
        "baseline": baseline,
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_data = fetch_all_prices()
    if not all_data:
        print("No data fetched. Exiting.")
        sys.exit(1)

    # Extract SPY data for adjusted return signals, remove from signal scanning
    spy_data = all_data.pop("SPY", None)
    if spy_data is not None:
        print(f"  SPY benchmark: {len(spy_data)} days loaded")
    else:
        print("  Warning: SPY data unavailable, adjusted return signals disabled")

    all_signals = scan_signals(all_data, spy_data=spy_data)
    win_rates = compute_win_rates(all_signals)

    print("Computing random baseline...")
    baseline = compute_baseline(all_data)
    print(f"  Baseline: {baseline['total']} entries, +5% wr={baseline['wr5']}%, +10% wr={baseline['wr10']}%")

    output = build_json(all_signals, win_rates, baseline)

    out_path = OUT_DIR / "signals.json"
    out_path.write_text(json.dumps(output, separators=(",", ":")))
    size_kb = out_path.stat().st_size / 1024
    print(f"Wrote {out_path} ({size_kb:.1f} KB)")
    print(f"  {len(output['signals'])} buy signals, generated at {output['generated_at']}")


if __name__ == "__main__":
    main()

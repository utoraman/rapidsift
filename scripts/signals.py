"""
RapidSift Signal Detection — shared module.

All signal detection logic lives here. Both the Telegram notifier and the
dashboard JSON generator import from this module so thresholds, filters,
and new signal types only need to be changed in one place.

Signal types:
    macd            MACD crossover (trend-filtered: buy only above SMA20)
    volume_spike    2x+ volume spike (trend-filtered: buy only above SMA20)
    gap_and_go      3%+ gap on 1.5x+ volume
    rel_strength    New 20-day high, 1%+ breakout, 1.3x+ volume, above SMA20
    earnings_drift  Post-catalyst drift (gap>=5%, vol>=3x → continuation next day)

Disabled (data showed no edge over random baseline):
    rsi             Removed — EV +0.29pp, profit factor 0.59x
    ma_crossover    Removed — WR 60.9%, worse than 62.5% random baseline
"""

import math


# ── Technical Indicators ─────────────────────────────────────────────────────

def compute_rsi(closes, period=14):
    """Wilder's RSI."""
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
    """Simple moving average."""
    sma = [float('nan')] * len(closes)
    for i in range(period - 1, len(closes)):
        sma[i] = sum(closes[i - period + 1:i + 1]) / period
    return sma


def compute_ema(data, period):
    """Exponential moving average."""
    ema = [data[0]]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        ema.append(data[i] * k + ema[-1] * (1 - k))
    return ema


# ── Core Signal Detection ────────────────────────────────────────────────────

def detect_signals_at(closes, opens, highs, lows, volumes, idx):
    """
    Detect signals at a given bar index.

    Works on raw lists so it can be called in a loop over historical bars
    (for backfill/validation) or on just the latest bar (for live alerts).

    Args:
        closes, opens, highs, lows, volumes: price/volume lists (full history)
        idx: the bar index to evaluate (uses data up to and including idx)

    Returns:
        List of signal dicts, each with 'type', 'direction', 'detail'.
        Caller is responsible for adding 'ticker', 'date', 'price', etc.
    """
    signals = []
    if idx < 1:
        return signals

    latest_close = closes[idx]
    prev_close = closes[idx - 1]
    c = closes[:idx+1]

    # Pre-compute SMAs used by multiple signals
    sma20 = compute_sma(c, 20) if len(c) >= 20 else [float('nan')] * len(c)
    above_sma20 = not math.isnan(sma20[-1]) and latest_close > sma20[-1]

    # ── MACD crossover (trend-filtered: buy only above SMA20) ──
    if len(c) >= 35:
        ema12 = compute_ema(c, 12)
        ema26 = compute_ema(c, 26)
        macd = [ema12[i] - ema26[i] for i in range(len(c))]
        sig_line = compute_ema(macd, 9)
        prev_d = macd[-2] - sig_line[-2]
        curr_d = macd[-1] - sig_line[-1]
        if prev_d < 0 and curr_d > 0 and above_sma20:
            signals.append({"type": "macd", "direction": "buy",
                           "detail": "MACD bullish crossover (above SMA20)"})
        elif prev_d > 0 and curr_d < 0:
            signals.append({"type": "macd", "direction": "sell",
                           "detail": "MACD bearish crossover"})

    # ── Volume spike (trend-filtered: buy only above SMA20) ──
    if idx >= 20:
        avg_vol = sum(volumes[idx-20:idx]) / 20
        if avg_vol > 0 and volumes[idx] / avg_vol >= 2.0:
            ratio = volumes[idx] / avg_vol
            if latest_close < opens[idx]:
                if above_sma20:
                    signals.append({"type": "volume_spike", "direction": "buy",
                                   "detail": f"Volume spike {ratio:.1f}x (dip in uptrend)"})
            else:
                signals.append({"type": "volume_spike", "direction": "sell",
                               "detail": f"Volume spike {ratio:.1f}x (surge)"})

    # ── Gap-and-go momentum ──
    if idx >= 20:
        gap_pct = ((opens[idx] - prev_close) / prev_close) * 100
        avg_vol = sum(volumes[idx-20:idx]) / 20
        vol_ratio = volumes[idx] / avg_vol if avg_vol > 0 else 0
        if gap_pct >= 3.0 and vol_ratio >= 1.5:
            signals.append({"type": "gap_and_go", "direction": "buy",
                           "detail": f"Gap up {gap_pct:+.1f}% on {vol_ratio:.1f}x volume"})
        elif gap_pct <= -3.0 and vol_ratio >= 1.5:
            signals.append({"type": "gap_and_go", "direction": "sell",
                           "detail": f"Gap down {gap_pct:+.1f}% on {vol_ratio:.1f}x volume"})

    # ── Relative strength breakout ──
    # New 20-day high with conviction: >1% breakout, >1.3x volume, above SMA20
    if idx >= 20:
        high_20d = max(highs[idx-20:idx])
        breakout_pct = ((highs[idx] - high_20d) / high_20d) * 100
        avg_vol = sum(volumes[idx-20:idx]) / 20
        vol_ratio = volumes[idx] / avg_vol if avg_vol > 0 else 0
        if highs[idx] > high_20d and above_sma20 and breakout_pct >= 1.0 and vol_ratio >= 1.3:
            signals.append({"type": "rel_strength", "direction": "buy",
                           "detail": f"New 20-day high ({breakout_pct:+.1f}% above prev, {vol_ratio:.1f}x vol)"})

    return signals


# ── Catalyst / Earnings Drift ────────────────────────────────────────────────

def detect_catalyst_days(all_data):
    """
    Detect probable earnings/catalyst days from market signature.

    A catalyst day is identified by: gap >= 5% AND volume >= 3x the 20-day
    average. Catches earnings reactions, FDA decisions, M&A, and other major
    catalysts without relying on external calendar APIs.

    Args:
        all_data: dict of {ticker: DataFrame} with columns
                  [close, open, high, low, volume, date]

    Returns:
        dict: ticker -> set of integer indices where a catalyst occurred
    """
    catalysts = {}
    for ticker, df in all_data.items():
        closes = df["close"].tolist()
        opens = df["open"].tolist()
        volumes = df["volume"].tolist()
        n = len(closes)
        catalyst_indices = set()
        for i in range(21, n):
            gap = abs((opens[i] - closes[i-1]) / closes[i-1]) * 100
            avg_vol = sum(volumes[i-20:i]) / 20
            vol_ratio = volumes[i] / avg_vol if avg_vol > 0 else 0
            if gap >= 5.0 and vol_ratio >= 3.0:
                catalyst_indices.add(i)
        if catalyst_indices:
            catalysts[ticker] = catalyst_indices
    return catalysts


def detect_catalyst_single(closes, opens, volumes, idx):
    """
    Check if a specific bar was a catalyst day (for single-ticker use).
    Returns True if bar at idx qualifies as a catalyst event.
    """
    if idx < 21:
        return False
    gap = abs((opens[idx] - closes[idx-1]) / closes[idx-1]) * 100
    avg_vol = sum(volumes[idx-20:idx]) / 20
    vol_ratio = volumes[idx] / avg_vol if avg_vol > 0 else 0
    return gap >= 5.0 and vol_ratio >= 3.0


def detect_earnings_drift(closes, opens, volumes, idx):
    """
    Check for post-catalyst drift at bar idx.

    Fires on the day AFTER a catalyst day if the move continues in the
    same direction (PEAD-style drift).

    Returns:
        Signal dict or None.
    """
    if idx < 22:
        return None
    # Was yesterday a catalyst day?
    if not detect_catalyst_single(closes, opens, volumes, idx - 1):
        return None

    catalyst_gap = ((opens[idx-1] - closes[idx-2]) / closes[idx-2]) * 100
    drift_ret = ((closes[idx] - closes[idx-1]) / closes[idx-1]) * 100

    if catalyst_gap > 0 and drift_ret >= 0:
        return {
            "type": "earnings_drift",
            "direction": "buy",
            "detail": f"Post-catalyst drift: catalyst gap {catalyst_gap:+.1f}%, "
                      f"day-2 move {drift_ret:+.1f}%",
        }
    elif catalyst_gap < 0 and drift_ret <= 0:
        return {
            "type": "earnings_drift",
            "direction": "sell",
            "detail": f"Post-catalyst drop: catalyst gap {catalyst_gap:+.1f}%, "
                      f"day-2 move {drift_ret:+.1f}%",
        }
    return None


# ── Constants ────────────────────────────────────────────────────────────────

# ── Sector Mapping ──────────────────────────────────────────────────────────

def load_sector_map():
    """
    Load ticker → sector ETF mapping from data/sectors.yaml.
    Returns dict: ticker -> sector ETF symbol (e.g. 'NVDA' -> 'XLK').
    """
    import yaml
    from pathlib import Path
    sector_path = Path(__file__).parent.parent / "data" / "sectors.yaml"
    if not sector_path.exists():
        return {}
    with open(sector_path) as f:
        data = yaml.safe_load(f)
    mapping = {}
    for etf, tickers in data.get("sectors", {}).items():
        for t in tickers:
            mapping[str(t)] = etf
    return mapping


# All sector ETFs that need to be fetched for sector_drop signals
SECTOR_ETFS = ["XLK", "XLC", "XLY", "XLF", "XLE", "XLV", "XLI",
               "XLB", "XLP", "XLU", "XLRE", "BITO"]


# Signal types that count toward confluence (strong edge signals only)
STRONG_TYPES = {"adjusted_drop", "sector_drop", "rel_strength",
                "gap_and_go", "earnings_drift"}
